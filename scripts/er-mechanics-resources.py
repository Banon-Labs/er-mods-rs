#!/usr/bin/env python3
"""Elden Ring player resources from the installed regulation: max HP, FP, stamina, equip load,
effective attributes after talismans/armor/great runes, equip-load tier, and rune cost per level.

Everything numeric is read from `regulation.bin` through `scripts/er-param-read.py`. The formulas
are the executable's, traced in the 1.16.2 named Ghidra dump and checked byte-for-byte against the
installed 1.17.1 image; see `docs/er-mechanics/resources.md` for the addresses and the evidence
labels (`VERIFIED`, `INFERRED`, `SITE`).

Arithmetic is emulated in single precision where the game uses `float`, so truncation boundaries
land where the game's do.

  python3 scripts/er-mechanics-resources.py --selftest
  python3 scripts/er-mechanics-resources.py --corpus [--show 20]
  python3 scripts/er-mechanics-resources.py --stats vig=40,mnd=20,vit=30,str=20,dex=20,int=9,fth=9,arc=9 \\
      --talisman "Erdtree's Favor +2" --armor "Fire Knight Helm" --great-rune "Radahn's Great Rune"
  python3 scripts/er-mechanics-resources.py --tables
"""
import argparse
import importlib.util
import json
import math
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('er_param_read', os.path.join(_HERE, 'er-param-read.py'))
PR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PR)

CORPUS = os.path.expanduser('~/.cache/er-build-planner/builds.jsonl')

# CalcCorrectGraph rows the executable passes to `PerformCalcCorrection` (1.16.2 addresses; each
# constant is re-found in the 1.17.1 image by `scripts/find-deobf-bytes.py`).
GRAPH_HP = 100          # PerformVigorCalcCorrection   0x140687570, `mov edx,0x64`
GRAPH_FP = 101          # PerformMindCalcCorrection    0x140687640, `mov edx,0x65`
GRAPH_STAMINA = 104     # PerformStaminaCalcCorrection 0x1406876d0, `mov edx,0x68`
GRAPH_EQUIP_LOAD = 220  # CalculateEffectiveEndurance  0x14068d000, `mov edx,0xdc`
GRAPH_LEVEL_COST = 200  # CalculateLevelUpCost         0x140686370, `mov edx,0xc8`

# GetWeightType 0x14068c630 compares burden/maxLoad against these `.rdata` floats
# (0x14329e678, 0x143b33d50, 0x143b33d54; 1.17.1: 0x1432a1938, 0x143b37d60, 0x143b37d64).
WEIGHT_OVER = 1.0
WEIGHT_HEAVY = 0.699999988079071
WEIGHT_MEDIUM = 0.30000001192092896
WEIGHT_TYPES = {0: 'super light', 1: 'light', 2: 'medium', 3: 'heavy', 4: 'overloaded'}

STAT_KEYS = ('vig', 'mnd', 'vit', 'str', 'dex', 'int', 'fth', 'arc')
# SpEffectParam field per attribute, in `CollectStatBuffs` 0x1404f3fd0 order. `addVitalityStatus`
# is also summed there but feeds the unused fifth slot, so it is left out.
STAT_FIELDS = {'vig': 'addLifeForceStatus', 'mnd': 'addWillpowerStatus', 'vit': 'addEndureStatus',
               'str': 'addStrengthStatus', 'dex': 'addDexterityStatus', 'int': 'addMagicStatus',
               'fth': 'addFaithStatus', 'arc': 'addLuckStatus'}
RATE_FIELDS = ('maxHpRate', 'maxMpRate', 'maxStaminaRate', 'equipWeightChangeRate')
SP_FIELDS = list(RATE_FIELDS) + list(STAT_FIELDS.values()) + ['spCategory', 'stateInfo']

# Great rune -> SpEffect "Effect 0" row (SpEffectParam row names; the goods rows 191..196 carry no
# refId, so the link itself is `INFERRED` from names and the +10 stride).
GREAT_RUNE_SPEFFECT = {
    "Godrick's Great Rune": 600, "Radahn's Great Rune": 610, "Morgott's Great Rune": 620,
    "Rykard's Great Rune": 630, "Mohg's Great Rune": 640, "Malenia's Great Rune": 650,
}

CLASS_ROWS = {  # CharaInitParam row = archetype + 3000 (crates/er-build-import-core/src/class.rs)
    'Vagabond': 3000, 'Warrior': 3001, 'Hero': 3002, 'Bandit': 3003, 'Astrologer': 3004,
    'Prophet': 3005, 'Confessor': 3006, 'Samurai': 3007, 'Prisoner': 3008, 'Wretch': 3009,
    'Idus Knight': 3010, 'Heavy Knight': 3011,
}
CLASS_FIELDS = ('baseVit', 'baseWil', 'baseEnd', 'baseStr', 'baseDex', 'baseMag', 'baseFai', 'baseLuc')


def f32(x):
    """Round to IEEE single precision, as an SSE `float` register would hold it."""
    return struct.unpack('<f', struct.pack('<f', x))[0]


class Model:
    """Regulation-backed resource model. Build once; every query is pure arithmetic after that."""

    def __init__(self, regulation=None):
        files = PR.load(regulation)

        def rows(stem, fields=None):
            rs, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return rs

        self.graphs = {r['id']: r for r in rows('CalcCorrectGraph')}
        self.speffects = {r['id']: r for r in rows('SpEffectParam', SP_FIELDS)}
        acc_fields = ['weight', 'refId', 'refCategory', 'accessoryGroup'] + [
            f'residentSpEffectId{i}' for i in range(1, 5)]
        self.accessories = {r['id']: r for r in rows('EquipParamAccessory', acc_fields)}
        pro_fields = ['weight', 'residentSpEffectId', 'residentSpEffectId2', 'residentSpEffectId3',
                      'protectorCategory']
        self.protectors = {r['id']: r for r in rows('EquipParamProtector', pro_fields)}
        self.classes = {r['id']: r for r in rows('CharaInitParam', ['soulLv'] + list(CLASS_FIELDS))
                        if 3000 <= r['id'] < 3100}
        self.accessory_by_name = self._by_name('EquipParamAccessory', self.accessories)
        self.protector_by_name = self._by_name('EquipParamProtector', self.protectors)

    @staticmethod
    def _by_name(stem, table):
        out = {}
        for rid, name in sorted(PR.row_names(stem).items()):
            if name and rid in table and name not in out:
                out[name] = rid
        return out

    # -- CalcCorrectGraph --------------------------------------------------------------------
    def calc_correct(self, graph_id, x):
        """`PerformCalcCorrection` 0x140690f30, in single precision.

        Clamp x to stageMaxVal4; x <= 0 returns stageMaxGrowVal0; pick the first stage i with
        x <= stageMaxVal[i+1]; t = (x - lo) / span; exponent e = adjPt_maxGrowVal[i]:
        e >= 0 -> t**e, e < 0 -> 1 - (1 - t)**(-e); value = grow[i] + curve * (grow[i+1] - grow[i]),
        then clamped between grow[i] and grow[i+1] (the minss/maxss block at 0x1406910ce..0x140691175).
        """
        r = self.graphs[graph_id]
        sv = [f32(r[f'stageMaxVal{i}']) for i in range(5)]
        g = [f32(r[f'stageMaxGrowVal{i}']) for i in range(5)]
        a = [f32(r[f'adjPt_maxGrowVal{i}']) for i in range(5)]
        x = f32(float(x))
        if sv[4] <= x:
            x = sv[4]
        if not x > 0.0:
            return g[0]
        i = 0
        while i < 4 and not x <= sv[i + 1]:
            i += 1
        if i == 4:
            raise ValueError(f'CalcCorrectGraph {graph_id}: stages not ascending')
        lo, span = sv[i], f32(sv[i + 1] - sv[i])
        dg = f32(g[i + 1] - g[i])
        e = a[i]
        if e < 0.0:
            p = f32(math.pow(f32(f32(span - f32(x - lo)) / span), f32(e * -1.0)))
            v = f32(g[i] + f32(f32(1.0 - p) * dg))
        else:
            p = f32(math.pow(f32(f32(x - lo) / span), e))
            v = f32(g[i] + f32(p * dg))
        return min(max(v, min(g[i], g[i + 1])), max(g[i], g[i + 1]))

    # -- effects ------------------------------------------------------------------------------
    def speffects_for(self, talismans=(), armor=(), great_rune=None, extra_speffects=()):
        """SpEffect rows applied by equipped talismans / armor / an active great rune.

        Talisman: `refId` (refCategory 2 = SpEffect) plus residentSpEffectId1..4. Armor:
        residentSpEffectId, 2, 3. Unknown names are reported back instead of guessed.
        """
        ids, unknown = [], []
        groups = {}
        for name in talismans:
            rid = self.accessory_by_name.get(name)
            if rid is None:
                unknown.append(('talisman', name))
                continue
            a = self.accessories[rid]
            # Two talismans sharing accessoryGroup (Arsenal Charm / Great-Jar's Arsenal, the
            # +N tiers) cannot be worn together in game; reported, still applied as given.
            if a['accessoryGroup'] in groups:
                unknown.append(('accessoryGroup conflict', f"{groups[a['accessoryGroup']]} / {name}"))
            groups[a['accessoryGroup']] = name
            ids += [a['refId'] if a['refCategory'] == 2 else -1] + [
                a[f'residentSpEffectId{i}'] for i in range(1, 5)]
        for name in armor:
            rid = self.protector_by_name.get(name)
            if rid is None:
                unknown.append(('armor', name))
                continue
            p = self.protectors[rid]
            ids += [p['residentSpEffectId'], p['residentSpEffectId2'], p['residentSpEffectId3']]
        if great_rune:
            sid = GREAT_RUNE_SPEFFECT.get(great_rune)
            if sid is None:
                unknown.append(('great rune', great_rune))
            else:
                ids.append(sid)
        ids += list(extra_speffects)
        return [self.speffects[i] for i in ids if i > 0 and i in self.speffects], unknown

    @staticmethod
    def rate(effects, field):
        """Product over entries, skipping rates <= 0 (`CS::SpecialEffect::GetMaxHpRate` 0x1404fbe60
        and siblings). The multiplayer factor `GetMPLevelCorrection` is 1 unless SpEffect 590/592
        is active, i.e. solo play."""
        r = 1.0
        for e in effects:
            v = f32(e[field])
            if v > 0.0:
                r = f32(r * v)
        return r

    @staticmethod
    def effective_stats(base, effects):
        """`CalculateStatus` 0x1407c3670: base + summed add*Status, capped at 99 (no lower clamp)."""
        out = {}
        for k in STAT_KEYS:
            add = sum(int(e[STAT_FIELDS[k]]) for e in effects)
            out[k] = min(int(base[k]) + add, 99)
        return out

    # -- resources ----------------------------------------------------------------------------
    def resources(self, base, talismans=(), armor=(), great_rune=None, extra_speffects=()):
        effects, unknown = self.speffects_for(talismans, armor, great_rune, extra_speffects)
        eff = self.effective_stats(base, effects)
        hp_base = int(self.calc_correct(GRAPH_HP, eff['vig']))
        fp_base = int(self.calc_correct(GRAPH_FP, eff['mnd']))
        st_base = int(self.calc_correct(GRAPH_STAMINA, eff['vit']))
        load_base = self.calc_correct(GRAPH_EQUIP_LOAD, eff['vit'])
        hp_rate = self.rate(effects, 'maxHpRate')
        fp_rate = self.rate(effects, 'maxMpRate')
        st_rate = self.rate(effects, 'maxStaminaRate')
        load_rate = self.rate(effects, 'equipWeightChangeRate')
        return {
            'effective_stats': eff,
            'max_hp': int(f32(hp_rate * hp_base)), 'max_hp_base': hp_base, 'hp_rate': hp_rate,
            'max_fp': int(f32(fp_rate * fp_base)), 'max_fp_base': fp_base, 'fp_rate': fp_rate,
            'max_stamina': int(f32(st_rate * st_base)), 'max_stamina_base': st_base,
            'stamina_rate': st_rate,
            'max_equip_load': f32(load_rate * load_base), 'max_equip_load_base': load_base,
            'equip_load_rate': load_rate,
            'unknown_items': unknown,
            # Same inputs in double precision, the way the planner computes; used only to
            # attribute corpus disagreements to rounding.
            'planner_f64': {
                'max_hp': hp_base * self.rate_f64(effects, 'maxHpRate'),
                'max_fp': fp_base * self.rate_f64(effects, 'maxMpRate'),
                'max_stamina': st_base * self.rate_f64(effects, 'maxStaminaRate'),
            },
        }

    @staticmethod
    def rate_f64(effects, field):
        r = 1.0
        for e in effects:
            v = round(e[field], 6)
            if v > 0.0:
                r *= v
        return r

    # -- equip load tiers ---------------------------------------------------------------------
    @staticmethod
    def weight_type(burden, max_load):
        """`CalculatePlayerWeight` 0x14068bda0 + `GetWeightType` 0x14068c630 for a player (the
        stateInfo 115/102 overrides are not carried by any 1.17.1 SpEffect row)."""
        ratio = f32(burden / max_load) if max_load > 0.0 else 0.0
        if ratio > WEIGHT_OVER:
            return 4
        if ratio > WEIGHT_HEAVY:
            return 3
        if ratio > WEIGHT_MEDIUM:
            return 2
        return 1

    def burden(self, weapons=(), armor=(), talismans=()):
        """Sum of weights as `CalculateEquipmentWeight` 0x140247b80 accumulates it (six weapon
        slots, four armor, the talisman slots; arrows and bolts are outside the loop). Weapon
        weights are passed as numbers because this module does not load EquipParamWeapon names."""
        w = 0.0
        for x in weapons:
            w = f32(w + f32(x))
        for name in armor:
            rid = self.protector_by_name.get(name)
            if rid is not None:
                w = f32(w + f32(self.protectors[rid]['weight']))
        for name in talismans:
            rid = self.accessory_by_name.get(name)
            if rid is not None:
                w = f32(w + f32(self.accessories[rid]['weight']))
        return w

    # -- runes --------------------------------------------------------------------------------
    def level_up_cost(self, level):
        """`CalculateLevelUpCost` 0x140686370, `level` being the level bought: the stat screen
        calls it with `PlayerGameData.level + 1` (0x1407c83a0, field 0x271a)."""
        r = self.graphs[GRAPH_LEVEL_COST]
        x = f32(float(level + 80))
        t = f32(x - f32(r['boundry_value']))
        t = t if t >= 0.0 else 0.0
        slope = f32(f32(f32(r['boundry_inclination_soul']) * t) + f32(r['init_inclination_soul']))
        sq = f32(math.pow(x, 2.0))
        return int(f32(f32(sq * slope) + f32(r['adjustment_value'])))

    @staticmethod
    def level_up_cost_f64(level):
        """The planner's double-precision spelling of the same curve (`SITE`)."""
        x = level + 80
        return int((max(0, x - 92) * 0.02 + 0.1) * (x * x) + 1)

    def runes_to_next(self, rl):
        return self.level_up_cost(rl + 1)

    def runes_total(self, class_level, rl):
        return sum(self.level_up_cost(n + 1) for n in range(class_level, rl))

    def class_base(self, name):
        r = self.classes[CLASS_ROWS[name]]
        return r['soulLv'], dict(zip(STAT_KEYS, (r[f] for f in CLASS_FIELDS)))


# ---------------------------------------------------------------------------------------------
def _equipped(slots):
    return [s['name'] for s in slots or [] if s.get('equipIndex') is not None and s.get('name')]


def corpus(model, path=CORPUS, show=10):
    """Agreement with the planner's stored `computed` block (`SITE` values).

    Each comparison lands in one bucket: `exact` (game model equals the planner), `rounding` (the
    planner's own double-precision arithmetic on the same inputs reproduces its number, so the gap
    is float32 vs float64 at a truncation boundary), `skipped` (an item name this module cannot
    resolve), or `other`, which is listed.
    """
    tallies = {}
    diffs = {}
    buckets = ('exact', 'rounding', 'skipped', 'other')

    def tally(key, bucket, info=None):
        t = tallies.setdefault(key, dict.fromkeys(buckets, 0))
        t[bucket] += 1
        if bucket == 'other':
            diffs.setdefault(key, []).append(info)

    def classify(got, want, planner_style, clean):
        if got == want:
            return 'exact'
        if not clean:
            return 'skipped'
        if planner_style is not None and planner_style == want:
            return 'rounding'
        return 'other'

    unknown_names = {}
    for line in open(path, encoding='utf-8'):
        rec = json.loads(line)
        b = rec['build']
        st, comp = b.get('stats') or {}, b.get('computed') or {}
        if not all(isinstance(st.get(k), int) and st.get(k) > 0 for k in STAT_KEYS):
            continue
        if isinstance(st.get('rl'), int):
            implied = sum(st[k] for k in STAT_KEYS) - 79
            if st['rl'] == implied:
                tally('rl = sum - 79', 'exact')
            elif st['rl'] == 0:
                tally('rl = sum - 79', 'skipped')
            else:
                tally('rl = sum - 79', 'other', (rec['id'], st['rl'], implied))
        tal = _equipped((b.get('talismans') or {}).get('slots'))
        arm = []
        for part in (b.get('protectors') or {}).values():
            arm += _equipped(part.get('slots'))
        res = model.resources(st, tal, arm, b.get('greatRune'))
        for kind, name in res['unknown_items']:
            unknown_names[(kind, name)] = unknown_names.get((kind, name), 0) + 1
        clean = not res['unknown_items']
        pf = res['planner_f64']

        def info(got, want):
            return (rec['id'], got, want, st, tal, arm, b.get('greatRune'))

        # The planner truncates FP (`t|0`) before storing and stores HP/stamina untruncated
        # through `pv(x) = +x.toFixed(8)`; mirror both so its number is reproduced exactly.
        for key, got, want, planner_style in [
                ('maxHealth (int)', res['max_hp'], comp.get('maxHealth'), int(round(pf['max_hp'], 8))),
                ('maxFP', res['max_fp'], comp.get('maxFP'), int(pf['max_fp'])),
                ('maxStamina (int)', res['max_stamina'], comp.get('maxStamina'),
                 int(round(pf['max_stamina'], 8)))]:
            if want is None:
                continue
            want_i = int(want + 1e-9)
            tally(key, classify(got, want_i, planner_style, clean), info(got, want))
        if 'maxEquipLoad' in comp:
            want = comp['maxEquipLoad']
            ok = abs(res['max_equip_load'] - want) <= 0.001
            tally('maxEquipLoad (+-0.001)', 'exact' if ok else ('skipped' if not clean else 'other'),
                  info(round(res['max_equip_load'], 4), want))
        runes = comp.get('runes')
        cls = b.get('characterClass')
        if runes and cls in CLASS_ROWS and isinstance(st.get('rl'), int) and st['rl'] > 0:
            lvl, _ = model.class_base(cls)
            total_f64 = sum(model.level_up_cost_f64(n + 1) for n in range(lvl, st['rl']))
            tally('runes.total', classify(model.runes_total(lvl, st['rl']), runes.get('total'),
                                          total_f64, True),
                  (rec['id'], model.runes_total(lvl, st['rl']), runes.get('total'), cls, st['rl']))
            if st['rl'] < 713:
                nxt = runes.get('toNextLevel')
                tally('runes.toNextLevel vs game level rl+1',
                      classify(model.runes_to_next(st['rl']), nxt,
                               model.level_up_cost_f64(st['rl'] + 1), True),
                      (rec['id'], model.runes_to_next(st['rl']), nxt, st['rl']))
                tally('runes.toNextLevel vs level rl+2',
                      classify(model.level_up_cost(st['rl'] + 2), nxt,
                               model.level_up_cost_f64(st['rl'] + 2), True),
                      (rec['id'], model.level_up_cost(st['rl'] + 2), nxt, st['rl']))
    print('agreement with the planner computed block '
          '(exact / planner double-rounding / skipped: unresolved name or rl 0 / other):')
    for k, t in tallies.items():
        n = sum(t.values())
        print(f'  {k:38s} n={n:5d}  exact {t["exact"]:5d} ({100.0 * t["exact"] / n:6.2f}%)  '
              f'rounding {t["rounding"]:4d}  skipped {t["skipped"]:4d}  other {t["other"]:4d}')
    if unknown_names:
        print('unresolved item names (not in the Smithbox row-name set; 1.17 items):')
        for (kind, name), n in sorted(unknown_names.items(), key=lambda kv: -kv[1]):
            print(f'  {kind:10s} {name!r} x{n}')
    for k, ds in diffs.items():
        print(f'-- {k}: {len(ds)} disagreements, first {min(show, len(ds))}')
        for d in ds[:show]:
            print('   ', d)
    return tallies, diffs


def selftest(model):
    fails = []

    def check(label, got, want):
        if got != want:
            fails.append(f'{label}: got {got!r}, want {want!r}')

    # Anchor points of the graphs (stage boundaries are exact).
    for graph, x, want in [(GRAPH_HP, 1, 300), (GRAPH_HP, 25, 800), (GRAPH_HP, 40, 1450),
                           (GRAPH_HP, 60, 1900), (GRAPH_HP, 99, 2100), (GRAPH_FP, 1, 50),
                           (GRAPH_FP, 99, 450), (GRAPH_STAMINA, 1, 80), (GRAPH_STAMINA, 99, 170)]:
        check(f'graph {graph} at {x}', int(model.calc_correct(graph, x)), want)
    check('equip load at 99', model.calc_correct(GRAPH_EQUIP_LOAD, 99), 160.0)
    check('equip load at 8', model.calc_correct(GRAPH_EQUIP_LOAD, 8), 45.0)
    check('equip load above cap', model.calc_correct(GRAPH_EQUIP_LOAD, 150), 160.0)
    # Planner FP and stamina tables (`SITE`, notifications-BSZ1DATO.js `nr`/`tr`), a sample.
    for x, want in [(10, 78), (20, 121), (35, 200), (50, 300), (60, 350), (80, 401)]:
        check(f'FP at {x}', int(model.calc_correct(GRAPH_FP, x)), want)
    for x, want in [(10, 96), (30, 130), (50, 155), (51, 155), (70, 161), (98, 169)]:
        check(f'stamina at {x}', int(model.calc_correct(GRAPH_STAMINA, x)), want)
    # Level-up costs: level 1 -> 2 is 673 runes; 150 -> 151 per the game's own argument.
    check('cost of level 2', model.level_up_cost(2), 673)
    check('cost of level 151', model.level_up_cost(151), 153680)
    # Class identity: sum of base attributes - 79 == starting level, every class row.
    for name in CLASS_ROWS:
        lvl, base = model.class_base(name)
        check(f'{name} level identity', sum(base.values()) - 79, lvl)
    # Talisman decode.
    r = model.resources(dict.fromkeys(STAT_KEYS, 99), ["Erdtree's Favor +2"], [], "Radahn's Great Rune")
    check('99 vig + favor+2 + radahn hp', r['max_hp'], 2511)
    check('99 end + favor+2 + radahn stamina', r['max_stamina'], 215)
    check('99 end + favor+2 load', round(r['max_equip_load'], 3), 172.8)
    # 1900 * f32(1.04) = 1975.99993 rounds to the float 1975.99988 and truncates to 1975; the
    # planner's double arithmetic shows 1976.
    r = model.resources(dict(dict.fromkeys(STAT_KEYS, 10), vig=60), ["Erdtree's Favor +2"])
    check('60 vig + favor+2 hp (float32 truncation)', r['max_hp'], 1975)
    r = model.resources(dict.fromkeys(STAT_KEYS, 10), ["Radagon's Soreseal"], [], None)
    check('soreseal vig', r['effective_stats']['vig'], 15)
    check('soreseal mind untouched', r['effective_stats']['mnd'], 10)
    r = model.resources(dict.fromkeys(STAT_KEYS, 97), ["Radagon's Soreseal"], [], "Godrick's Great Rune")
    check('stat cap 99', r['effective_stats']['str'], 99)
    # Tier boundaries are strict greater-than.
    check('30% is light', Model.weight_type(30.0, 100.0), 1)
    check('70% is medium', Model.weight_type(70.0, 100.0), 2)
    check('70.01% is heavy', Model.weight_type(70.01, 100.0), 3)
    check('100% is heavy', Model.weight_type(100.0, 100.0), 3)
    check('100.01% is overloaded', Model.weight_type(100.01, 100.0), 4)
    if fails:
        print('selftest FAILED:')
        for f in fails:
            print('  ' + f)
        return 1
    print('selftest ok')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--regulation')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--corpus', nargs='?', const=CORPUS)
    ap.add_argument('--show', type=int, default=10)
    ap.add_argument('--tables', action='store_true', help='print HP/FP/stamina/load/cost for 1..99')
    ap.add_argument('--stats', help='vig=..,mnd=..,vit=..,str=..,dex=..,int=..,fth=..,arc=..')
    ap.add_argument('--talisman', action='append', default=[])
    ap.add_argument('--armor', action='append', default=[])
    ap.add_argument('--great-rune')
    ap.add_argument('--burden', type=float, help='equipped weight, to report the load tier')
    a = ap.parse_args()
    model = Model(a.regulation)
    if a.selftest:
        return selftest(model)
    if a.corpus:
        corpus(model, a.corpus, a.show)
        return 0
    if a.tables:
        print('stat  hp    fp   stam  load     cost(level=stat)')
        for x in range(1, 100):
            print(f'{x:3d} {int(model.calc_correct(GRAPH_HP, x)):5d} {int(model.calc_correct(GRAPH_FP, x)):4d} '
                  f'{int(model.calc_correct(GRAPH_STAMINA, x)):5d} {model.calc_correct(GRAPH_EQUIP_LOAD, x):7.3f} '
                  f'{model.level_up_cost(x):8d}')
        return 0
    if a.stats:
        base = dict.fromkeys(STAT_KEYS, 10)
        for kv in a.stats.split(','):
            k, v = kv.split('=')
            base[k.strip()] = int(v)
        res = model.resources(base, a.talisman, a.armor, a.great_rune)
        res['rl'] = sum(base[k] for k in STAT_KEYS) - 79
        res['runes_to_next'] = model.runes_to_next(res['rl'])
        if a.burden is not None:
            res['weight_type'] = WEIGHT_TYPES[Model.weight_type(a.burden, res['max_equip_load'])]
        print(json.dumps(res, indent=1))
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
