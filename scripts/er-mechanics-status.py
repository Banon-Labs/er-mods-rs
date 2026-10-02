#!/usr/bin/env python3
"""Elden Ring status build-up per hit, hits to proc and proc damage, offline, PvP focused.

Covers bleed, frost, poison, scarlet rot, sleep, madness and death blight. The chain was read
out of the named 1.16.2 Ghidra dump and re-found in `eldenring-deobf-1.17.1.bin`; the write-up
with every address is `docs/er-mechanics/status.md`. Labels in comments: `VERIFIED` (executable
or regulation), `INFERRED` (fits the data, consumer not traced), `COMMUNITY`, `MEASURED` (corpus).

  python3 scripts/er-mechanics-status.py "Uchigatana" --affinity Blood --level 25 \\
      --stats str=14,dex=20,arc=45 --slot r1_1
  python3 scripts/er-mechanics-status.py "Uchigatana" --level 25 --stats str=18,dex=40 \\
      --grease "Drawstring Blood Grease" --rl 150
  python3 scripts/er-mechanics-status.py --corpus --rl 150        # resistance distribution
  python3 scripts/er-mechanics-status.py --top --rl 150           # STR/Quality PvP weapons
  python3 scripts/er-mechanics-status.py --selftest

The chain per hit (`VERIFIED` unless marked):

  sources  = 13 hit SpEffect slots (`FUN_140d24b10`): 0..4 the AtkParam's five SpEffect ids,
             5..7 EquipParamWeapon.spEffectBehaviorId0..2 + ReinforceParamWeapon.spEffectId1..3,
             8..9 unused, 10..12 from the weapon gaitem; plus one grease row
             (attacker SpEffect with stateInfo 152/153 -> atkOccurrenceSpEffectId)
  status   = the row's stateInfo (2 poison, 5 rot, 6 bleed, 116 death, 260 frost, 436 sleep,
             437 madness), value = that status's *AttackPower field (`FUN_140d4ffd0`)
  per row  = int(value * finalStatus[s] * part * reqPenalty * atkCorrect) * defenderRate
             finalStatus[s] = arcane multiplier (`FUN_1406832a0`, 4 statuses) * durability
                              * weapon vsPlayerDmgCorrectRate_<s> when both sides are PvP
             reqPenalty     = 0.2 when the STR (x1.5 two-handed), DEX, INT or FTH requirement
                              is unmet
             atkCorrect     = only when the row has isUseStatusAilmentAtkPowerCorrect:
                              AtkParam.statusAilmentAtkPowerCorrectRate / 100, times
                              statusAilmentAtkPowerCorrectRate_byPoint / 100 for slots 0..4
                              and the grease row
  gauge    = starts at the defender's resistance, loses each row's int, procs when < 1,
             refills at PlayerCommonParam.resistRecoverPoint_<s>_Player per second, every frame
  proc     = the same SpEffect row applies: HP loss = maxHP * changeHpRate / 100 + changeHpPoint

Library interface for `scripts/er-builds-pvp.py` (and anything else):

  st  = load_module()                           # importlib; the file name is hyphenated
  t   = st.Tables()                             # one regulation read
  ws  = st.weapon_status(t, 'Uchigatana', 'Blood', 25, {'str': 14, 'dex': 20, 'arc': 45},
                         two_handed=False, pvp=True)
  hit = st.hit_buildup(t, ws, atk_row=900000, grease='Blood Grease')     # {status: int}
  dfn = st.corpus_defender(st.corpus_rows(rl=150))                        # median resist + HP
  out = st.status_per_hit(t, ws, attack, dfn, grease=None, interval=None)
        # attack is an `er-mechanics-attacks.weapon_attacks()` row: uses atk_row, hit_windows,
        # other_hitboxes; returns {status: {'buildup', 'hits_to_proc', 'proc_hp',
        # 'hp_per_hit', 'hp_per_hit_discrete', 'damage_taken_mult', 'resistance'}}
  dfs = st.Defenders(t, st.corpus_rows(rl=150))                           # every PvP build
  ex  = st.status_expected(t, ws, attack, dfs, react=(0, 1), stagger=0.3, chain=[...])
        # the ranking's number, by engagement: the hit plus its true combos (`chain`), carriers
        # bolus between engagements, non-carriers keep the gauge minus refill, one live proc per
        # status (`EXCLUSIVE_CATEGORY_MIN`), a DoT cut at the cure or the fight end; returns
        # {status: {'hp_per_hit', 'engagements_to_proc', 'proc_share', 'procs_per_hit', ...}}
"""
import argparse, collections, importlib.util, json, math, os, statistics, struct, sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), os.path.join(_HERE, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_module():
    """This file as a module (for callers that import it by path)."""
    return _sibling('er-mechanics-status')


AR = _sibling('er-mechanics-ar')
EPR = AR.EPR
CORPUS = Path.home() / '.cache/er-build-planner/builds.jsonl'
REPO = Path(_HERE).parent

# Resist-module index order (`CSChrResistModule` arrays at +0x10/+0x2c/+0x48/+0x64/+0x80/+0x9c,
# `FUN_14043daf0` dispatch): name, stateInfo, SpEffect attack field, arcane graph field,
# vsPlayer suffix, defender *DefDamageRate field, PlayerCommonParam recover spelling, corpus group.
STATUSES = [
    ('poison', 2, 'poizonAttackPower', 'correctType_Poison', 'Poison', 'poisonDefDamageRate', 'Poision', 'immunity'),
    ('scarlet_rot', 5, 'diseaseAttackPower', None, 'Disease', 'diseaseDefDamageRate', 'Desease', 'immunity'),
    ('bleed', 6, 'bloodAttackPower', 'correctType_Blood', 'Blood', 'bloodDefDamageRate', 'Blood', 'robustness'),
    ('death_blight', 116, 'curseAttackPower', None, 'Curse', 'curseDefDamageRate', 'Curse', 'vitality'),
    ('frost', 260, 'freezeAttackPower', None, 'Freeze', 'freezeDefDamageRate', 'Freeze', 'robustness'),
    ('sleep', 436, 'sleepAttackPower', 'correctType_Sleep', 'Sleep', 'sleepDefDamageRate', 'Sleep', 'focus'),
    ('madness', 437, 'madnessAttackPower', 'correctType_Madness', 'Madness', 'madnessDefDamageRate', 'Madness', 'focus'),
]
NAMES = [s[0] for s in STATUSES]
BY_STATE = {s[1]: s for s in STATUSES}
GROUPS = ('immunity', 'robustness', 'focus', 'vitality')

#: `FUN_14068d1b0` (1.16.2) / 0x14068e000 (1.17.1): status multiplier when HasStatsForWeapon is
#: false. Constant at 0x14329e64c (1.16.2) / 0x1432a190c (1.17.1), both 0.2.
REQ_PENALTY = 0.2
REQ_PENALTY_VA = {'eldenring-deobf.bin': 0x14329e64c, 'eldenring-deobf-1.17.1.bin': 0x1432a190c}
#: `FUN_140d2f930` returns 5: the per-status proc counter cap. The counter only feeds the NPC
#: ResistCorrectParam raise in `FUN_14043ea10`, which needs a NpcParam.
PROC_COUNT_CAP = 5
#: The hit-slot index below which the byPoint rate also applies (`cmp r12d,5` at 0x140448d1a).
ATK_SLOTS = 5
#: Grease buff rows carry these stateInfo values (`FUN_1404f71e0`: `stateInfo - 0x98 < 2`).
GREASE_BUFF_STATES = (152, 153)
TWO_HAND_STR = AR.TWO_HAND_STR_MULT

SP_FIELDS = ['stateInfo', 'changeHpRate', 'changeHpPoint', 'changeMpRate', 'changeMpPoint',
             'effectEndurance', 'motionInterval', 'isUseStatusAilmentAtkPowerCorrect',
             'atkOccurrenceSpEffectId', 'replaceSpEffectId', 'cycleOccurrenceSpEffectId',
             'slashDamageCutRate', 'blowDamageCutRate', 'thrustDamageCutRate', 'neutralDamageCutRate',
             'magicDamageCutRate', 'fireDamageCutRate', 'thunderDamageCutRate', 'darkDamageCutRate',
             'bCurrHPIndependeMaxHP', 'spCategory', 'categoryPriority',
             'effectTargetOpposeTarget', 'effectTargetFriendlyTarget', 'effectTargetSelfTarget',
             'effectTargetPcHorse', 'effectTargetAttacker', 'effectTargetPlayer', 'effectTargetAI',
             'effectTargetSelf', 'effectTargetFriend', 'effectTargetEnemy', 'effectTargetLive',
             'effectTargetGhost', 'maxHpRate'] \
    + [s[2] for s in STATUSES] + [s[5] for s in STATUSES]
#: Who a hit row can land on (status.md section 1b). `CheckApplyConditions` passes the row's
#: byte +0x16c bits 0..2 (`effectTargetOpposeTarget`, `FriendlyTarget`, `SelfTarget`) to
#: `canTeamTypeHitAnother` 0x14051ac10 / 0x14051ba10 through
#: `validateTeamTypeRelationshipWithSpEffect` 0x14051a9f0 / 0x14051b7f0, with (attacker, victim):
#: the same call, argument order and relation table the damage check `FUN_1404443e0` makes with
#: `AtkParam` +0x81 bits 0..2. A hostile victim (an invader and a host) needs
#: `effectTargetOpposeTarget`. Bit 3 (`effectTargetPcHorse`) restricts the row to a player's
#: horse (`IsSomeonesHorse` 0x1403f4370). `effectTargetAttacker` (+0x160 bit 1) sends the row to
#: the attacker instead (`CalculateDamage2` at 0x140448aed -> `FUN_1403e8b70`). The legacy
#: `effectTargetSelf`..`Ghost` byte (+0x15f), `effectTargetPlayer` and `effectTargetAI`
#: included, has no reader in either image.
TARGET_GATE_BYTES = {
    # movzx r8d, byte [rax+0x16c]; and r8d, 1  -> the opposeTarget argument
    'oppose': bytes.fromhex('440fb6806c0100004183e001'),
    # movzx eax, byte [rcx+0x16c]; shr eax, 3  -> effectTargetPcHorse
    'horse': bytes.fromhex('0fb6816c010000c1e803'),
}
TARGET_GATE_VA = {
    'eldenring-deobf.bin': {'oppose': 0x14051aa32, 'horse': 0x1404fc62a},
    'eldenring-deobf-1.17.1.bin': {'oppose': 0x14051b832, 'horse': 0x1404fd3fa},
}
#: `FUN_1404fc690` (1.16.2) / 0x1404fd460 (1.17.1), called from `CheckApplyConditions`
#: 0x1404fc4e0 / 0x1404fd2b0: a row with `spCategory` at or above this (`cmp r10w, 0x2710`) is
#: refused while any live entry has the same category. Every weapon, grease and AtkParam status
#: row is category 10003..10010, one per status, so while a proc is live the next row of that
#: status is refused by `CS::SpecialEffect::Apply`, and `FUN_1403fade0` returns before
#: `FUN_14043daf0`: no build-up reaches the gauge, no refresh, no second instance (`VERIFIED`).
EXCLUSIVE_CATEGORY_MIN = 10000
EXCLUSIVE_CATEGORY_BYTES = bytes.fromhex('b81027000066443bd0')    # mov eax, 0x2710; cmp r10w, ax
EXCLUSIVE_CATEGORY_VA = {'eldenring-deobf.bin': 0x1404fc6a3, 'eldenring-deobf-1.17.1.bin': 0x1404fd473}
ATK_FIELDS = ['statusAilmentAtkPowerCorrectRate', 'statusAilmentAtkPowerCorrectRate_byPoint',
              'disableHitSpEffect'] + [f'spEffectId{i}' for i in range(5)]


class Tables:
    """Everything the status chain reads, keyed by row id. Reuses `er-mechanics-ar.Tables`."""

    def __init__(self, regulation=None, ar_tables=None):
        self.ar = ar_tables or AR.Tables(regulation)
        files = EPR.load(regulation)

        def by_id(stem, fields=None):
            rs, _, _ = EPR.rows(EPR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rs}

        self.sp = by_id('SpEffectParam', SP_FIELDS)
        self.atk = by_id('AtkParam_Pc', ATK_FIELDS)
        pc = next(iter(by_id('PlayerCommonParam').values()))
        self.recover = {s[0]: pc[f'resistRecoverPoint_{s[6]}_Player'] for s in STATUSES}
        self.recover_enemy = {s[0]: pc[f'resistRecoverPoint_{s[6]}_Enemy'] for s in STATUSES}
        self.sp_names = EPR.row_names('SpEffectParam')
        self._greases = None
        goods = by_id('EquipParamGoods', ['refId_default', 'refCategory', 'goodsUseAnim'])
        goods_names = EPR.row_names('EquipParamGoods')
        self.bolus = boluses(self.sp, goods, goods_names)

    def greases(self):
        """{name: on-attack row id} for every grease buff row that carries a status."""
        if self._greases is None:
            out = {}
            for rid, r in self.sp.items():
                if r['stateInfo'] not in GREASE_BUFF_STATES or r['atkOccurrenceSpEffectId'] <= 0:
                    continue
                hit = self.sp.get(r['atkOccurrenceSpEffectId'])
                name = (self.sp_names.get(rid) or '').replace('[Item] ', '')
                if hit and row_status(hit) and 'Grease' in name and name.endswith('- Right'):
                    out[name[:-len(' - Right')]] = r['atkOccurrenceSpEffectId']
            self._greases = out
        return self._greases


def cure_status(row):
    """(status name, value) when the row carries a negative build-up for its status, else None.

    A negative amount passes `FUN_14043e630` unchanged (it returns early when amount <= 0) and
    `FUN_14043d8a0` then does `gauge -= amount` and clamps to the resistance, so -99999 refills
    the gauge to full (`VERIFIED`, 1.16.2). `FUN_14043daf0` also dispatches stateInfo 118 to the
    death blight gauge (Rejuvenating Boluses' row)."""
    for s in STATUSES:
        if row.get(s[2], 0) < 0:
            return s[0], row[s[2]]
    return None


def boluses(sp, goods, goods_names):
    """{status: {'goods', 'name', 'speffects', 'amount', 'use_anim'}} for every `* Boluses` good.

    EquipParamGoods.refId_default (refCategory 2) is a SpEffect; its `replaceSpEffectId` chain
    ends on a row whose stateInfo is the status and whose build-up is -99999 (`VERIFIED`
    regulation). No bolus row grants resistance: every row in the chain has effectEndurance 0
    or 0.1 and no change*ResistPoint."""
    out = {}
    for gid, g in goods.items():
        name = goods_names.get(gid) or ''
        if not name.endswith('Boluses') or g['refCategory'] != 2:
            continue
        rid, chain = g['refId_default'], []
        while rid > 0 and rid in sp and rid not in chain:
            chain.append(rid)
            cs = cure_status(sp[rid])
            if cs:
                out.setdefault(cs[0], {'goods': gid, 'name': name, 'speffects': chain, 'amount': cs[1],
                                       'use_anim': g['goodsUseAnim']})
                break
            rid = sp[rid]['replaceSpEffectId']
    return out


def row_status(row):
    """(status name, build-up value) a SpEffect row applies, or None.

    `FUN_14043daf0` dispatches on stateInfo alone, then reads the matching *AttackPower field;
    a value in any other status field of the same row is ignored (`VERIFIED`)."""
    s = BY_STATE.get(row.get('stateInfo'))
    if not s or row.get(s[2], 0) <= 0:
        return None
    return s[0], row[s[2]]


def has_stats_for_weapon(wep, stats, two_handed):
    """`PlayerGameData::HasStatsForWeapon` 0x14068db30: STR (x1.5 two-handed), DEX, INT, FTH.

    Arcane is not checked (`VERIFIED`)."""
    s = int((TWO_HAND_STR if two_handed else 1.0) * stats['str'])
    return (wep['properStrength'] <= s and wep['properAgility'] <= stats['dex']
            and wep['properMagic'] <= stats['int'] and wep['properFaith'] <= stats['fth'])


def weapon_status(t, weapon, affinity='Standard', level=0, stats=None, two_handed=False, pvp=True):
    """The weapon's own status rows (hit slots 5..7) with every factor that does not depend on
    the attack. `value` is the per-hit build-up at an attack correction of 100."""
    stats = AR.normalise_stats(stats)
    tab = t.ar
    base_id = tab.find_weapon(weapon, affinity)
    wep = tab.weapons[base_id]
    reinf = tab.reinforce[wep['reinforceTypeId'] + level]
    two = two_handed or wep['wepType'] in AR.ALWAYS_TWO_HANDED_WEP_TYPES
    if wep.get('isDualBlade'):
        two = False
    ok = has_stats_for_weapon(wep, stats, two)
    arc_rate = wep['correctLuck'] * reinf['correctLuckRate']
    finals = {}
    for name, _, _, graph, vs, _, _, _ in STATUSES:
        # `FUN_1406832a0` out[6..12]: the arcane wrapper `FUN_140690b40` for four statuses, 1.0
        # for the rest; times the durability factor (1.0 for an unbroken weapon, `INFERRED`).
        arc = AR.stat_multiplier(tab, wep['properLuck'], stats['arc'], arc_rate, wep[graph]) if graph else 1.0
        # `CalculateDamageCorrections` 0x140684d70: weapon vsPlayer rate when both are PvP.
        rate = wep.get(f'vsPlayerDmgCorrectRate_{vs}', 1.0) if pvp else 1.0
        finals[name] = {'arcane': arc, 'vs_player': rate}
    sources = []
    for k in range(3):
        rid = wep.get(f'spEffectBehaviorId{k}', -1) + reinf.get(f'spEffectId{k + 1}', 0)
        if rid <= 0 or rid not in t.sp:                 # `0 < id` gate in CalculateDamage2
            continue
        rs = row_status(t.sp[rid])
        if not rs:
            continue
        name, base = rs
        f = finals[name]
        sources.append({'slot': 5 + k, 'speffect': rid, 'status': name, 'base': base,
                        'arcane': f['arcane'], 'vs_player': f['vs_player'],
                        'req': 1.0 if ok else REQ_PENALTY,
                        'uses_atk_correct': bool(t.sp[rid]['isUseStatusAilmentAtkPowerCorrect']),
                        'value': base * f['arcane'] * f['vs_player'] * (1.0 if ok else REQ_PENALTY)})
    return {'weapon': tab.names.get(base_id), 'id': base_id + level, 'level': level, 'two_handed': two,
            'has_stats': ok, 'finals': finals, 'req': 1.0 if ok else REQ_PENALTY, 'sources': sources}


def hostile_target_refusal(row):
    """Why a hit row cannot build up on a hostile victim (a PvP opponent), or None.

    See `TARGET_GATE_BYTES`. Victim-state conditions (`wetConditionDepth`, the above-shadow
    test) are not refusals of the row as such and are not modelled; no status row a weapon,
    grease, buff or `AtkParam_Pc` reaches sets either."""
    if not row.get('effectTargetOpposeTarget', 1):
        return 'effectTargetOpposeTarget is 0'
    if row.get('effectTargetPcHorse', 0):
        return 'effectTargetPcHorse: horses only'
    if row.get('effectTargetAttacker', 0):
        return 'effectTargetAttacker: applied to the attacker'
    return None


def _row_amount(t, ws, rid, atk, both_rates, part_rate, defender_rates):
    row = t.sp.get(rid)
    if not row or hostile_target_refusal(row):
        return None
    rs = row_status(row)
    if not rs:
        return None
    name, base = rs
    f = ws['finals'][name]
    mult = f['arcane'] * f['vs_player'] * ws['req'] * part_rate
    if row['isUseStatusAilmentAtkPowerCorrect'] and atk:
        mult *= atk['statusAilmentAtkPowerCorrectRate'] / 100.0
        if both_rates:
            mult *= atk['statusAilmentAtkPowerCorrectRate_byPoint'] / 100.0
    # `FUN_14043e630`: (1 - guard cut) * raw * product of the defender's *DefDamageRate, then
    # `(int)fVar10` before `FUN_14043d8a0`. Guarded hits are not modelled.
    return name, int(base * mult * (defender_rates or {}).get(name, 1.0))


def hit_buildup(t, ws, atk_row=None, grease=None, part_rate=1.0, defender_rates=None):
    """Build-up one damaging hit applies, per status, as the defender's gauge receives it.

    `atk_row` is the AtkParam_Pc row of the hit (None: correction 100 and no AtkParam SpEffects).
    `grease` is a name from `Tables.greases()` or an on-attack SpEffect id. `defender_rates`
    is {status: product of the defender's *DefDamageRate}, 1.0 when omitted."""
    atk = t.atk.get(atk_row) if atk_row is not None else None
    out = {}
    if atk and atk['disableHitSpEffect']:
        return out                                      # all 13 slots -1 (`FUN_140d24b10`)
    rows = []
    if atk:
        rows += [(atk[f'spEffectId{i}'], True) for i in range(5) if atk[f'spEffectId{i}'] > 0]
    rows += [(s['speffect'], False) for s in ws['sources']]
    if grease is not None:
        gid = grease if isinstance(grease, int) else t.greases()[grease]
        rows.append((gid, True))                        # attacker-occurrence path: both rates
    for rid, both in rows:
        r = _row_amount(t, ws, rid, atk, both, part_rate, defender_rates)
        if r:
            out[r[0]] = out.get(r[0], 0) + r[1]
    return out


def hits_to_proc(per_hit, resistance, interval=None, recover=0.0, limit=200):
    """Hits until the gauge drops below 1 (`FUN_14043d8a0`).

    `per_hit` is the int build-up of one hit. With `interval` (seconds between hits) the gauge
    refills by `recover` points per second between hits (`FUN_14043e440`; seconds are
    `INFERRED` from `FD4Time::SetScaled` on the frame delta)."""
    if per_hit <= 0 or resistance <= 0:
        return None
    gauge = float(resistance)
    for n in range(1, limit + 1):
        if n > 1 and interval:
            gauge = min(float(resistance), gauge + recover * interval)
        if int(gauge) - per_hit < 1:
            return n
        gauge -= per_hit
    return None


def proc_effect(t, rid, max_hp):
    """HP an applied status row costs a player (`FUN_1404fb920` -> `FUN_1404f7d00`).

    Per tick: maxHP * changeHpRate / 100 + changeHpPoint, scaled by the defender's
    *DamageRate for bleed/frost/sleep/madness (100 on players without an effect). Ticks =
    effectEndurance / motionInterval, at least one (`INFERRED` from the row timing)."""
    row = t.sp[rid]
    per_tick = max_hp * row['changeHpRate'] / 100.0 + row['changeHpPoint']
    dur, iv = row['effectEndurance'], row['motionInterval']
    ticks = max(1, int(dur / iv)) if iv > 0 and dur > 0 else 1
    if row['changeHpRate'] == 0 and row['changeHpPoint'] == 0:
        ticks = 0
    return {'speffect': rid, 'hp_per_tick': per_tick, 'ticks': ticks, 'hp_total': per_tick * ticks,
            'duration': dur, 'interval': iv, 'fp_pct': row['changeMpRate'], 'fp_flat': row['changeMpPoint'],
            'damage_taken_mult': row['slashDamageCutRate'], 'replace': row['replaceSpEffectId']}


def proc_row_for(t, ws, status, grease=None):
    """The row whose effect applies when `status` procs: the weapon's own row first, else the
    grease's. Which of two rows applies when both carry the status was not traced: the proc
    happens inside whichever row's application empties the gauge."""
    for s in ws['sources']:
        if s['status'] == status and not hostile_target_refusal(t.sp[s['speffect']]):
            return s['speffect']
    if grease is not None:
        gid = grease if isinstance(grease, int) else t.greases()[grease]
        rs = row_status(t.sp[gid])
        if rs and rs[0] == status and not hostile_target_refusal(t.sp[gid]):
            return gid
    return None


def use_buildup(t, ws, attack, grease=None):
    """{status: build-up of one use of `attack`}: every hit window and every extra hitbox row
    counts as one application (`INFERRED`: each damaging hit runs `CalculateDamage2` once)."""
    per = {}
    rows = [(attack.get('atk_row'), max(1, len(attack.get('hit_windows') or [])))]
    rows += [(o.get('atk_row'), 1) for o in attack.get('other_hitboxes') or [] if o.get('atk_row')]
    for atk_row, n in rows:
        for name, v in hit_buildup(t, ws, atk_row, grease).items():
            per[name] = per.get(name, 0) + v * n
    return per


def status_per_hit(t, ws, attack, defender, grease=None, interval=None):
    """Per status: build-up of one use of `attack`, hits to proc against `defender` and the HP
    that is worth per hit.

    `attack`: a row of `er-mechanics-attacks.weapon_attacks()` (atk_row, hit_windows,
    other_hitboxes) or a bare dict {'atk_row': id}. Every hit window and every extra hitbox row
    counts as one application (`INFERRED`: each damaging hit runs `CalculateDamage2` once).
    `defender`: {'resist': {group: value}, 'hp': max HP} (see `corpus_defender`).
    `hp_per_hit` = proc HP * build-up / resistance (the proc spread linearly over the gauge);
    `hp_per_hit_discrete` = proc HP / hits_to_proc (whole hits, from a full gauge)."""
    out = {}
    for name, b in use_buildup(t, ws, attack, grease).items():
        grp = next(s[7] for s in STATUSES if s[0] == name)
        resist = defender['resist'][grp]
        src = proc_row_for(t, ws, name, grease)
        pe = proc_effect(t, src, defender['hp']) if src else {'hp_total': 0.0, 'damage_taken_mult': 1.0}
        n = hits_to_proc(int(b), resist, interval, t.recover[name])
        out[name] = {'buildup': b, 'resistance': resist, 'hits_to_proc': n, 'proc_hp': pe['hp_total'],
                     'hp_per_hit': pe['hp_total'] * b / resist if resist else 0.0,
                     'hp_per_hit_discrete': pe['hp_total'] / n if n else 0.0,
                     'damage_taken_mult': pe.get('damage_taken_mult', 1.0), 'proc_speffect': src}
    return out


# --------------------------------------------------------------------------------------------
# corpus

#: Same PvP rule as `er-builds-optimize.is_pvp`.
PVP_TAGS = {'Invasions', 'Duels', 'Co-op/Gank', '2v2', 'Ladder', 'Fishing'}
LEVEL_OFFSET = 79
ATTRS = ('vig', 'mnd', 'vit', 'str', 'dex', 'int', 'fth', 'arc')


def is_pvp(build):
    if build.get('isPvE') is True:
        return False
    return build.get('isPvE') is False or bool(set(build.get('tags') or []) & PVP_TAGS)


def corpus_rows(path=CORPUS, rl=150, window=10, pvp_only=True):
    """Corpus builds of RL rl +- window whose stats add up: stats, resistances, max HP, weapons."""
    out = []
    for line in Path(path).read_text().splitlines():
        b = json.loads(line)['build']
        st = b.get('stats') or {}
        if not all(isinstance(st.get(k), int) for k in ATTRS + ('rl',)):
            continue
        if not rl - window <= st['rl'] <= rl + window or sum(st[k] for k in ATTRS) - LEVEL_OFFSET != st['rl']:
            continue
        if pvp_only and not is_pvp(b):
            continue
        c = b.get('computed') or {}
        res = c.get('resistances')
        if not res or not c.get('maxHealth'):
            continue
        right = [s for s in (b.get('inventory') or {}).get('slots') or [] if s.get('equipIndex') in (0, 1, 2)]
        # Quick items: `items.tools.slots`. A build that lists none says nothing about what it
        # carries (717 of the 1074 RL 150 PvP builds, `MEASURED`), so `tools` is None there.
        tools = ((b.get('items') or {}).get('tools') or {}).get('slots') or []
        talismans = (b.get('talismans') or {}).get('slots') or []
        # The planner's flask split, `items.flasks` {level, crimson, cerulean}, or None.
        flasks = (b.get('items') or {}).get('flasks') or None
        out.append({'stats': st, 'resist': {g: res[g] for g in GROUPS}, 'hp': c['maxHealth'],
                    'two_handed': bool(b.get('is2h')), 'right': right, 'tags': b.get('tags') or [],
                    'tools': {s['name'] for s in tools} if tools else None,
                    'talismans': sorted({s['name'] for s in talismans}), 'flasks': flasks})
    return out


def quantiles(vals, qs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    v = sorted(vals)
    return {q: v[min(len(v) - 1, int(q * len(v)))] for q in qs} if v else {}


def corpus_defender(rows, q=0.5):
    """The defender at quantile `q` of each resistance group and of max HP."""
    return {'resist': {g: quantiles([r['resist'][g] for r in rows], (q,))[q] for g in GROUPS},
            'hp': quantiles([r['hp'] for r in rows], (q,))[q], 'n': len(rows)}


# --------------------------------------------------------------------------------------------
# expected status value over the corpus: refill between hits, the defender spread, talismans,
# boluses

#: TAE frames per second (`INFERRED`, the rate er-mechanics-attacks.py uses).
FPS = 30.0
#: The clip a bolus plays: `EquipParamGoods.goodsUseAnim` 0 is `a000_050000` "Goods Use (Eating)"
#: (`COMMUNITY`: the Nyasu 1.17 frame data lists the bolus behavior ids 3050..3092 on that clip's
#: goods event; the HKS mapping from goodsUseAnim to the clip was not traced).
GOODS_USE_ANIM = 50000
#: TAE event `ConsumeCurrentGoods`: the frame the item's SpEffect is invoked.
TAE_CONSUME_GOODS = 65
#: Its frame in `a000_050000` (`TAE` 31, and 31 in the Nyasu 1.17 data), when the TAE is absent.
CURE_FRAME_FALLBACK = 31
#: JumpTable ids that let a defender start an item: input 30 "Input - Goods" or 87 "Input -
#: Common", overlapping cancel 31 "Cancel - Goods" (TAE template names; same overlap rule as
#: the attack recovery in er-mechanics-attacks.py).
GOODS_INPUTS, GOODS_CANCELS = (30, 87), (31,)
#: Modelling choices, both `INFERRED`: frames of slack past the earliest possible cure over
#: which a cure goes from never to always possible (a human reaction), and the frames a carrier
#: takes to press the bolus once they can after an engagement.
CURE_SLACK_RAMP = 15.0
DOT_CURE_DELAY = 15.0

# The engagement model (`status_expected`). A defender takes the first hit of an engagement and
# every follow-up that is a true combo; the first follow-up they can roll or guard ends it. Then
# they disengage: a carrier of the matching bolus uses it before the next engagement (gauge back
# to full, an active poison / rot / frost ended), everyone else carries the gauge over and it
# refills for the time between engagements. The shape is the user's description of how PvP
# defenders play ("hit once, roll the second hit, run or roll or bolus, and then bolus").

#: Neutral time between two engagements, seconds (`INFERRED`). Measuring it needs a timeline of
#: landed hits from real fights (a damage-hook log of invasions, or frame-counted footage): the
#: median time between one cluster of landed hits and the next.
ENGAGEMENT_SECONDS = 5.0
#: Damage per landed hit the fight length is derived from (`MEASURED`): the median `dmg` of the
#: best slot of the 324 weapons `er-builds-pvp.py --rl 150 --sort score --top 400` ranked before
#: this model (the mean over the 1074 RL 150 defenders). `Defenders.fight_engagements` = the
#: corpus median max HP / this, rounded up: 1946 / 471.5 -> 5 engagements of one landed hit.
#: Flasks and the status damage itself are not in it, so it is a short fight (`INFERRED`).
FIGHT_REF_DAMAGE = 471.5
#: Weight of a follow-up whose combo verdict is `tie` (same frame as the defender's escape; the
#: order of the hit and the defender's HKS update decides it, `INFERRED` even odds).
COMBO_LAND = {'true': 1.0, 'tie': 0.5, 'no': 0.0}
#: Engagements simulated when counting engagements to proc without a fight end.
ENGAGEMENT_LIMIT = 60
#: Talisman `change*ResistPoint` field per status (`VERIFIED` regulation field names).
RESIST_POINT = {'poison': 'changePoisonResistPoint', 'scarlet_rot': 'changeDiseaseResistPoint',
                'bleed': 'changeBloodResistPoint', 'frost': 'changeFreezeResistPoint',
                'sleep': 'changeSleepResistPoint', 'madness': 'changeMadnessResistPoint',
                'death_blight': 'changeCurseResistPoint'}
GROUP_OF = {s[0]: s[7] for s in STATUSES}
#: Attacker talismans a proc switches on: (er-mechanics-talismans `TIMED` key, buff SpEffect,
#: statuses whose presence starts it). The presence stateInfo gate is `VERIFIED`
#: (docs/er-mechanics/talismans.md); that a proc on the defender fires the presence bullet is
#: `INFERRED`, and madness for Aged One's and sleep for St. Trina's are `COMMUNITY`. None of them
#: touches build-up or proc HP: attacker SpEffects never scale status (section 1).
EXULTATIONS = {"Lord of Blood's Exultation": ('lord_of_blood', 321601, ('bleed',)),
               "Kindred of Rot's Exultation": ('kindred_of_rot', 321701, ('poison', 'scarlet_rot')),
               "Aged One's Exultation": ('aged_one', 20380601, ('madness',)),
               "St. Trina's Smile": ('st_trina', 20381601, ('sleep',))}

_LAZY = {}


def _fa():
    if 'fa' not in _LAZY:
        _LAZY['fa'] = _sibling('er-mechanics-frame-advantage')
    return _LAZY['fa']


def _tal():
    if 'tal' not in _LAZY:
        _LAZY['tal'] = _sibling('er-mechanics-talismans')
        _LAZY['tal_t'] = _LAZY['tal'].Talismans()
    return _LAZY['tal'], _LAZY['tal_t']


def _goods_open(events):
    """First frame (clip time) an item can start in one TAE clip, or None."""
    A = _fa().ATTACKS
    win = {'input': [], 'cancel': [], 'unresolved_early': 0}
    for e in events:
        if e.type == A.TAE_JUMP_TABLE:
            jid = struct.unpack_from('<i', e.params, 0)[0]
            if struct.unpack_from('<H', e.params, A.JUMP_TABLE_STATE_GATE_OFFSET)[0]:
                continue
            if jid in GOODS_INPUTS:
                win['input'].append((e.start, e.end))
            if jid in GOODS_CANCELS:
                win['cancel'].append((e.start, e.end))
        elif e.type == A.TAE_JUMP_TABLE_EARLY:
            jid, early = struct.unpack_from('<hh', e.params, 0)
            if jid in GOODS_CANCELS and early == A.EARLY_DEFAULT:
                w = A._early_interval(e, 0.0)
                if w:
                    win['cancel'].append(w)
    t = A._first_open(win, True)
    return None if t is None else round(t * FPS)


def goods_ready(level):
    """Frames from a hit until the defender can start an item, for the reaction at `level`.

    Level 0 is the additive flinch, which does not interrupt (`frame-advantage.md`): 0. Other
    levels: the earliest goods window over that level's `a000` damage clips (`TAE`, the same
    clips `er-mechanics-frame-advantage.reaction` reads). Measured: small 12, middle 25,
    large 35, push 40, minimum 13."""
    if level in _LAZY.setdefault('ready', {}):
        return _LAZY['ready'][level]
    fa = _fa()
    val = 0
    if level and level in fa.LEVELS:
        anims = fa.common_tae()
        clips = fa.LEVELS[level][2]
        opens = [_goods_open(anims[c]) for c in clips if anims and c in anims]
        opens = [o for o in opens if o is not None]
        if opens:
            val = min(opens)
        else:
            r = fa.reaction(level)
            val = (r or {}).get('guard') or 0
    _LAZY['ready'][level] = val
    return val


def cure_frame():
    """Frames from pressing a bolus to its SpEffect: `ConsumeCurrentGoods` in `a000_050000`."""
    if 'cure' not in _LAZY:
        anims = _fa().common_tae()
        ev = (anims or {}).get(GOODS_USE_ANIM) or []
        starts = [round(e.start * FPS) for e in ev if e.type == TAE_CONSUME_GOODS]
        _LAZY['cure'] = min(starts) if starts else CURE_FRAME_FALLBACK
    return _LAZY['cure']


def cure_ready(level):
    """Frames from a hit at reaction `level` until a bolus pressed at once takes effect."""
    return goods_ready(level) + cure_frame()


def cure_opportunity(gap, level):
    """Chance that the defender can land a cure between two hits `gap` frames apart: 0 when the
    next hit arrives before the bolus takes effect, rising to 1 over `CURE_SLACK_RAMP` frames of
    slack (`INFERRED` shape). The engagement model needs it only to confirm that no bolus fits
    inside a true combo; between engagements a carrier always cures."""
    if not gap:
        return 0.0
    slack = gap - cure_ready(level)
    return 0.0 if slack <= 0 else min(1.0, slack / CURE_SLACK_RAMP)


def talisman_resist(names):
    """{status: resistance an always-on talisman SpEffect adds}, summed over `names`
    (`VERIFIED` regulation; the add is `CalcTotalResistance`'s spAdd, defense.md section 5)."""
    tal, tt = _tal()
    out = {s: 0 for s in RESIST_POINT}
    for n in names:
        x = tt.get(n)
        if not x:
            continue
        for sp in x.speffects:
            if sp.condition():
                continue
            for s, f in RESIST_POINT.items():
                out[s] += sp.get(f) or 0
    return out


def status_talismans():
    """{talisman: {status: add}} for every talisman whose closure changes a status resistance,
    and {talisman: statuses} for the attacker-side exultations."""
    tal, tt = _tal()
    out = {}
    for x in tt:
        adds = {}
        for sp in x.speffects:
            for s, f in RESIST_POINT.items():
                v = sp.get(f) or 0
                if v:
                    adds[s] = max(adds.get(s, 0), v)
        if adds:
            out[x.name] = adds
    return out


def bolus_share(t, rows):
    """{status: share of the builds that record quick items which carry that status's bolus}."""
    listed = [r for r in rows if r.get('tools') is not None]
    return {s: (sum(b['name'] in r['tools'] for r in listed) / len(listed) if listed else 0.0)
            for s, b in t.bolus.items()}


class Defenders:
    """The corpus as defenders of each status: groups of (resistance, bolus carry, count, summed
    max HP). Resistance is the planner's `computed.resistances` group, which includes armor and
    talismans (defense.md section 7: 92.6% agreement with `CalcTotalResistance`, `MEASURED`).
    Carry is 1 or 0 for a build that records quick items, else the measured share."""

    def __init__(self, t, rows, extra_resist=None):
        self.n = len(rows)
        self.share = bolus_share(t, rows)
        self.median_hp = quantiles([r['hp'] for r in rows], (0.5,)).get(0.5, 0.0)
        #: Engagements to empty one HP bar: the median defender's HP over `FIGHT_REF_DAMAGE`,
        #: rounded up, no flask drunk.
        self.fight_engagements = max(1, math.ceil(self.median_hp / FIGHT_REF_DAMAGE))
        #: Engagements a fight runs, the `status_expected` default: a count, or a schedule over
        #: fight points (`er-mechanics-buffs.fight_hits`) that the ranking sets once it has
        #: derived one. Until then, the one-bar count.
        self.fight_hits = self.fight_engagements
        self.groups = {}
        for s in NAMES:
            grp, bol, acc = GROUP_OF[s], t.bolus.get(s), {}
            add = (extra_resist or {}).get(s, 0)
            for r in rows:
                if bol is None:
                    c = 0.0
                elif r.get('tools') is None:
                    c = self.share[s]
                else:
                    c = 1.0 if bol['name'] in r['tools'] else 0.0
                key = (r['resist'][grp] + add, c)
                a = acc.setdefault(key, [0, 0.0])
                a[0] += 1
                a[1] += r['hp']
            self.groups[s] = [(k[0], k[1], v[0], v[1]) for k, v in acc.items()]


def combo_land(combo, stagger=0.0):
    """Chance a follow-up connects, from one frame-advantage `slot_profile` combo entry: its
    `on_intact` verdict when the defender's poise holds, `on_break` when it breaks, weighted by
    the stagger share of the hit before it (`COMBO_LAND` per verdict)."""
    def land(side):
        v = (combo or {}).get(side)
        return COMBO_LAND.get(v['verdict'], 0.0) if v else 0.0
    return (1.0 - stagger) * land('on_intact') + stagger * land('on_break')


def engagement_lengths(chain):
    """[(landed hits, probability)] of one engagement: the first hit lands, follow-up k lands
    with `chain[k]['p']` when every one before it did, and the first miss ends the engagement."""
    out, p = [], 1.0
    links = chain or []
    for k, link in enumerate(links):
        out.append((k + 1, p * (1.0 - link['p'])))
        p *= link['p']
        if p <= 0.0:
            break
    else:
        out.append((len(links) + 1, p))
    return [(n, w) for n, w in out if w > 0.0]


_SIM = {}


def simulate(builds, gaps, resistance, recover, lock, carrier, engagements, eng_s=ENGAGEMENT_SECONDS,
             fight_end=None, dot=False, ticks=1, interval=0.0, cure_s=0.0):
    """One defender through `engagements` engagements that each land the same hits.

    `builds` are the int build-ups of the landed hits, `gaps` the frames between them; engagement
    e starts at e * `eng_s` seconds. Per hit: the gauge refills since the previous hit
    (`FUN_14043e440`, `recover` per second); while a proc of this status is live (`lock` s, the
    proc row's effectEndurance) the row is refused (`EXCLUSIVE_CATEGORY_MIN`) and nothing
    happens; otherwise gauge -= b and a proc when it drops below 1, gauge back to full
    (`FUN_14043d8a0`). A carrier boluses after each engagement: gauge full, and the live proc
    ends `cure_s` after the last hit (the bolus ending poison / rot / frost is game text).
    A proc is credited ticks from its hit to the first of its expiry, the cure and `fight_end`:
    one on application, then one per `interval` when `dot` (`INFERRED` schedule); a proc after
    `fight_end` counts nothing. Returns (procs, credited ticks, first proc as (engagement,
    landed hits), both 1-based, or None)."""
    key = (builds, gaps, resistance, recover, lock, carrier, engagements, eng_s, fight_end, dot, ticks,
           interval, cure_s)
    if key in _SIM:
        return _SIM[key]
    full = float(resistance)
    gauge, t_last, lock_until = full, 0.0, -1.0
    procs, credited, first, landed = 0, 0, None, 0
    end = math.inf if fight_end is None else fight_end
    for e in range(engagements):
        t = e * eng_s
        live = []
        for j, b in enumerate(builds):
            if j:
                t += gaps[j - 1] / FPS
            landed += 1
            gauge = min(full, gauge + recover * (t - t_last))
            t_last = t
            if b <= 0 or t < lock_until:
                continue
            if int(gauge) - b < 1:
                gauge, lock_until = full, t + lock
                live.append(t)
                if first is None:
                    first = (e + 1, landed)
            else:
                gauge -= b
        cure = t + cure_s if carrier else math.inf
        if carrier:
            gauge, lock_until = full, min(lock_until, cure)
        for tp in live:
            if tp > end:
                continue
            procs += 1
            stop = min(tp + lock, cure, end)
            credited += min(ticks, int((stop - tp) / interval) + 1) if dot and interval > 0 else ticks
    _SIM[key] = (procs, credited, first)
    return _SIM[key]


def status_expected(t, ws, attack, dfs, grease=None, gap=None, react=(0, 0), stagger=0.0, chain=None,
                    engagements=None, eng_s=ENGAGEMENT_SECONDS):
    """Expected status value per landed hit of `attack` over the corpus `dfs` (`Defenders`), by
    engagement (the constants above `ENGAGEMENT_SECONDS`).

    `chain` is the follow-ups after `attack` in order, [{'attack': attack row, 'p': chance it
    lands when the one before did (`combo_land`), 'gap': frames from the previous hit}], empty
    when the defender can escape the next hit; `gap` stands in for a link without one. `react` is
    the defender's damage level with poise intact and broken, `stagger` the share that breaks: the
    carrier's cure lands `cure_ready` + `DOT_CURE_DELAY` frames after the engagement's last hit.
    A fight is `engagements` (default `dfs.fight_hits`) engagements `eng_s` seconds apart; a
    sequence is a schedule (one count per fight point), and each distinct count is simulated and
    weighted by how many points carry it, so `hp_per_hit` pools every point's procs over every
    point's landed hits.
    Per defender group, carriers (the group's carry share) and non-carriers are each run through
    `simulate` for every engagement length. Returns per status: `hp_per_hit` (credited proc HP
    over the fight / landed hits in it, all defenders), `procs_per_hit`, `hits_per_engagement`,
    `engagements_to_proc` and `proc_share` ({'carrier', 'non_carrier'}: mean engagements to the
    first proc over the defenders it procs on, without a fight end, and the share it ever procs
    on), `hits_to_proc` (landed hits to the first proc, mean over those), `lockout` (seconds a
    proc blocks the next), `proc_hp` (whole proc), `proc_hp_credited` (mean per credited proc)."""
    chain = list(chain or [])
    lengths = engagement_lengths(chain)
    mean_len = sum(n * w for n, w in lengths)
    sched = engagements or getattr(dfs, 'fight_hits', None) or dfs.fight_engagements
    if isinstance(sched, (tuple, list)):
        counts = collections.Counter(int(n) for n in sched)
        regimes = [(n, c / len(sched)) for n, c in sorted(counts.items())]
    else:
        regimes = [(int(sched), 1.0)]
    n_f = sum(n * w for n, w in regimes)
    cure_at = (1.0 - stagger) * cure_ready(react[0]) + stagger * cure_ready(react[1])
    cure_s = (cure_at + DOT_CURE_DELAY) / FPS
    per_use = [use_buildup(t, ws, attack, grease)] + [use_buildup(t, ws, c['attack'], grease) for c in chain]
    gaps = [c.get('gap') or gap or 0.0 for c in chain]
    names = [s for s in NAMES if any(u.get(s) for u in per_use)]
    hits_fight = dfs.n * n_f * mean_len
    out = {}
    for name in names:
        builds = [int(u.get(name, 0)) for u in per_use]
        src = proc_row_for(t, ws, name, grease)
        row = t.sp.get(src) if src else None
        rate = row['changeHpRate'] if row else 0.0
        point = row['changeHpPoint'] if row else 0.0
        pe = proc_effect(t, src, 0.0) if src else {'ticks': 0, 'interval': 0.0, 'damage_taken_mult': 1.0}
        ticks, iv = pe['ticks'], pe['interval']
        dot = ticks > 1
        lock = row['effectEndurance'] if row else 0.0
        bol = t.bolus.get(name)
        recover = t.recover[name]
        full = credited = procs = resist = 0.0
        weight = {'carrier': 0.0, 'non_carrier': 0.0}
        first_w = {'carrier': 0.0, 'non_carrier': 0.0}
        first_eng = {'carrier': 0.0, 'non_carrier': 0.0}
        first_hits = 0.0
        for r, carry, cnt, sum_hp in dfs.groups[name]:
            resist += r * cnt
            per_tick = sum_hp * rate / 100.0 + cnt * point      # summed over the group's builds
            full += per_tick * ticks
            c = carry if bol else 0.0
            for mode, w in (('carrier', c), ('non_carrier', 1.0 - c)):
                if w <= 0.0:
                    continue
                weight[mode] += w * cnt
                for n_hits, p_len in lengths:
                    b, g = tuple(builds[:n_hits]), tuple(gaps[:n_hits - 1])
                    k = w * p_len
                    for n_e, w_e in regimes:
                        end = (n_e - 1) * eng_s + sum(g) / FPS
                        pr, cr, _ = simulate(b, g, r, recover, lock, mode == 'carrier', n_e, eng_s, end, dot,
                                             ticks, iv, cure_s)
                        credited += w_e * k * per_tick * cr
                        procs += w_e * k * cnt * pr
                    _, _, first = simulate(b, g, r, recover, lock, mode == 'carrier', ENGAGEMENT_LIMIT,
                                           eng_s, None, dot, ticks, iv, cure_s)
                    if first:
                        first_w[mode] += k * cnt
                        first_eng[mode] += k * cnt * first[0]
                        first_hits += k * cnt * first[1]
        n_first = first_w['carrier'] + first_w['non_carrier']
        out[name] = {'buildup': builds[0], 'buildup_chain': builds, 'resistance': resist / dfs.n,
                     'hits_per_engagement': mean_len, 'engagement_lengths': lengths,
                     'fight_engagements': n_f, 'engagement_seconds': eng_s,
                     'hits_per_second': mean_len / eng_s,
                     'engagements_to_proc': {m: (first_eng[m] / first_w[m] if first_w[m] else None)
                                             for m in weight},
                     'proc_share': {m: (first_w[m] / weight[m] if weight[m] else None) for m in weight},
                     'hits_to_proc': first_hits / n_first if n_first else None,
                     'hp_per_hit': credited / hits_fight if hits_fight else 0.0,
                     'procs_per_hit': procs / hits_fight if hits_fight else 0.0,
                     'proc_hp': full / dfs.n, 'proc_hp_credited': credited / procs if procs else 0.0,
                     'dot': dot, 'ticks': ticks, 'lockout': lock, 'gap': gaps[0] if gaps else gap,
                     'cure_at': round(cure_at, 1), 'bolus': bol['name'] if bol else None,
                     'bolus_share': dfs.share.get(name), 'damage_taken_mult': pe['damage_taken_mult'],
                     'proc_speffect': src}
    return out


def exultation_uptime(t, talisman, status, gap=None):
    """(er-mechanics-talismans `active` key, share of this slot's hits inside the buff) for an
    exultation the attacker wears: procs per hit * hits that land within the buff's
    effectEndurance (the engagement model's `hits_per_second`, else one per `gap` frames),
    capped at 1."""
    key, buff, trig = EXULTATIONS[talisman]
    ppp = sum(status[s]['procs_per_hit'] for s in trig if s in status)
    hps = next((status[s].get('hits_per_second') for s in trig if s in status
                and status[s].get('hits_per_second')), None)
    if not hps and gap:
        hps = FPS / gap
    if not ppp or not hps:
        return key, 0.0
    return key, min(1.0, ppp * t.sp[buff]['effectEndurance'] * hps)


# --------------------------------------------------------------------------------------------
# reports


def _attacks():
    return _sibling('er-mechanics-attacks')


def show_weapon(t, a):
    stats = dict(kv.split('=') for kv in a.stats.split(',') if kv)
    ws = weapon_status(t, a.weapon, a.affinity, a.level, stats, a.two_handed, not a.pve)
    print(f"{ws['weapon']} +{ws['level']} ({ws['id']}) {'2H' if ws['two_handed'] else '1H'}"
          f"  requirements {'met' if ws['has_stats'] else 'unmet: x0.2'}")
    for s in ws['sources']:
        print(f"  slot {s['slot']} SpEffect {s['speffect']} {t.sp_names.get(s['speffect'], '')!s:40.40} "
              f"{s['status']:<12} {s['base']:>4} x arcane {s['arcane']:.4f} x pvp {s['vs_player']:.2f}"
              f" = {s['value']:.2f}{'' if s['uses_atk_correct'] else '  (ignores AtkParam rate)'}")
    rows = corpus_rows(rl=a.rl, window=a.window)
    dfn = corpus_defender(rows)
    dfs = Defenders(t, rows)
    print(f"defender: median of {dfn['n']} PvP builds RL {a.rl}+-{a.window}: "
          + ', '.join(f"{g} {v}" for g, v in dfn['resist'].items()) + f", HP {dfn['hp']:.0f}")
    ATK = _attacks()
    reg = ATK.Regulation(None)
    wid = t.ar.find_weapon(a.weapon, a.affinity)
    for atk in ATK.weapon_attacks(reg, wid, 'both' if a.two_handed else 'one', a.level):
        if not a.all_slots and atk['slot'].removeprefix('2h_') != a.slot:
            continue
        r = status_per_hit(t, ws, atk, dfn, a.grease, a.interval)
        button = 'r2' if atk['slot'].removeprefix('2h_').startswith('r2') else 'r1'
        gap = a.interval * FPS if a.interval else (atk.get('cancel_frame') or {}).get(button)
        ex = status_expected(t, ws, atk, dfs, a.grease, gap) if ws['sources'] or a.grease else {}
        rate = t.atk[atk['atk_row']]
        print(f"  {atk['label']:<18} atk {atk['atk_row']:>9} rate {rate['statusAilmentAtkPowerCorrectRate']:>3}"
              f"/{rate['statusAilmentAtkPowerCorrectRate_byPoint']:>3}  "
              + '  '.join(f"{k} {v['buildup']:.0f}/use, {v['hits_to_proc'] or '-'} to proc, "
                          f"proc {v['proc_hp']:.0f} HP, {v['hp_per_hit']:.1f} HP/use"
                          for k, v in r.items()))
        for k, v in ex.items():
            e, p = v['engagements_to_proc'], v['proc_share']
            print(f"  {'':<18} corpus-expected {k} (one-hit engagements, {v['fight_engagements']} per fight, "
                  f"{v['engagement_seconds']:.0f} s apart): engagements to proc carrier {_fmt(e['carrier'])} "
                  f"({100 * (p['carrier'] or 0):.0f}% ever), non-carrier {_fmt(e['non_carrier'])} "
                  f"({100 * (p['non_carrier'] or 0):.0f}% ever); {v['bolus']} carried "
                  f"{100 * (v['bolus_share'] or 0):.0f}%; lockout {v['lockout']:.0f} s; credited "
                  f"{v['proc_hp_credited']:.0f} of {v['proc_hp']:.0f} HP per proc, {v['hp_per_hit']:.1f} HP/use")


def _fmt(x):
    return '-' if x is None else f'{x:.1f}'


def show_corpus(a):
    rows = corpus_rows(rl=a.rl, window=a.window)
    print(f"{len(rows)} PvP builds RL {a.rl}+-{a.window} with computed resistances")
    for g in GROUPS + ('hp',):
        vals = [r['hp'] if g == 'hp' else r['resist'][g] for r in rows]
        qs = quantiles(vals)
        print(f"  {g:<11} " + '  '.join(f"p{int(q * 100)} {v:.0f}" for q, v in qs.items())
              + f"  mean {statistics.mean(vals):.1f}")
    t = Tables()
    listed = [r for r in rows if r['tools'] is not None]
    print(f"bolus carried, of the {len(listed)} builds that record quick items:")
    for s, share in sorted(bolus_share(t, rows).items(), key=lambda kv: -kv[1]):
        print(f"  {t.bolus[s]['name']:<22} {s:<13} {100 * share:5.1f}%")
    print("status-resistance talismans worn (defender side):")
    worn = {}
    for r in rows:
        for n in r['talismans']:
            worn[n] = worn.get(n, 0) + 1
    for n, adds in sorted(status_talismans().items(), key=lambda kv: -worn.get(kv[0], 0)):
        k = worn.get(n, 0)
        if k:
            print(f"  {n:<30} {k:4d} {100 * k / len(rows):5.1f}%  "
                  + ', '.join(f"{s} +{v}" for s, v in adds.items()))
    print("status-triggered attacker talismans worn: "
          + ', '.join(f"{n} {worn.get(n, 0)} ({100 * worn.get(n, 0) / len(rows):.1f}%)" for n in EXULTATIONS))


AFF_ALIASES = {'None': 'Standard', None: 'Standard', '': 'Standard'}
#: EquipParamWeapon.weaponCategory left out of the melee table: 8 catalysts, 10 bows,
#: 11 crossbows, 12 shields and torches, 13/14 ammunition (`MEASURED` on sample rows).
NON_MELEE_CATEGORIES = {8, 10, 11, 12, 13, 14}
TABLE_GREASES = ('Drawstring Blood Grease', 'Drawstring Freezing Grease')


def top_weapons(t, a):
    """The corpus's most used Heavy/Quality right-hand melee weapons at this RL (plus Standard
    ones in builds leaning on strength), with bleed and frost per R1 against the corpus median,
    bare and with each drawstring status grease."""
    rows = corpus_rows(rl=a.rl, window=a.window)
    dfn = corpus_defender(rows)
    counts = {}
    for r in rows:
        st = r['stats']
        strish = st['str'] >= st['dex'] or (st['str'] >= 30 and st['dex'] >= 30)
        for s in r['right']:
            aff = AFF_ALIASES.get(s.get('infusion'), s.get('infusion'))
            if aff not in ('Heavy', 'Quality') and not (aff == 'Standard' and strish):
                continue
            counts.setdefault((s['name'], aff), []).append(r)
    ATK = _attacks()
    reg = ATK.Regulation(None)
    out = []
    for (name, aff), builds in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        if len(out) >= a.top_n:
            break
        try:
            wid = t.ar.find_weapon(name, aff)
        except SystemExit:
            continue
        if t.ar.weapons[wid]['weaponCategory'] in NON_MELEE_CATEGORIES:
            continue
        level = t.ar.max_level(t.ar.weapons[wid]['reinforceTypeId'])
        med = {k: int(statistics.median(b['stats'][k] for b in builds)) for k in ('str', 'dex', 'int', 'fth', 'arc')}
        two = statistics.mean(b['two_handed'] for b in builds) >= 0.5
        ws = weapon_status(t, name, aff, level, med, two, True)
        atks = {x['slot'].removeprefix('2h_'): x for x in ATK.weapon_attacks(reg, wid, 'both' if two else 'one', level)}
        r1 = atks.get('r1_1')
        if not r1:
            continue
        res = status_per_hit(t, ws, r1, dfn, a.grease)
        greased = {g: status_per_hit(t, ws, r1, dfn, g) for g in TABLE_GREASES}
        out.append({'weapon': name, 'aff': aff, 'n': len(builds), 'two': two, 'stats': med, 'level': level,
                    'r1_rate': t.atk[r1['atk_row']]['statusAilmentAtkPowerCorrectRate'],
                    'r1_by_point': t.atk[r1['atk_row']]['statusAilmentAtkPowerCorrectRate_byPoint'],
                    'r1_hits': max(1, len(r1.get('hit_windows') or [])), 'status': res, 'greased': greased})
    if a.json:
        print(json.dumps(out, indent=1, default=str))
        return
    print(f"defender = median of {dfn['n']} PvP builds RL {a.rl}+-{a.window}: robustness "
          f"{dfn['resist']['robustness']}, HP {dfn['hp']:.0f}" + (f"; grease {a.grease}" if a.grease else ''))

    def cell(res, status):
        v = res.get(status)
        if not v:
            return '0 | -'
        return f"{v['buildup']:.0f} | {v['hits_to_proc'] or '-'} ({v['hp_per_hit']:.0f})"

    print('| weapon | affinity | builds | grip | R1 rate | bleed/R1 | R1s (HP/R1) | frost/R1 | R1s (HP/R1)'
          ' | + DS Blood Grease bleed | R1s (HP/R1) | + DS Freezing Grease frost | R1s (HP/R1) |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for o in out:
        print(f"| {o['weapon']} | {o['aff']} | {o['n']} | {'2H' if o['two'] else '1H'} | "
              f"{o['r1_rate']}/{o['r1_by_point']} | {cell(o['status'], 'bleed')} | {cell(o['status'], 'frost')} | "
              f"{cell(o['greased'][TABLE_GREASES[0]], 'bleed')} | {cell(o['greased'][TABLE_GREASES[1]], 'frost')} |")


# --------------------------------------------------------------------------------------------
# selftest


def _read_f32(image, va):
    p = REPO / image
    if not p.exists():
        return None
    with open(p, 'rb') as f:
        f.seek(va - 0x140000000)
        return struct.unpack('<f', f.read(4))[0]


def _plain_row(t, rate):
    """An AtkParam_Pc row with this status rate, no SpEffects of its own and hit SpEffects on."""
    return next(i for i, r in sorted(t.atk.items()) if r['statusAilmentAtkPowerCorrectRate'] == rate
                and r['statusAilmentAtkPowerCorrectRate_byPoint'] == 100 and not r['disableHitSpEffect']
                and all(r[f'spEffectId{k}'] <= 0 for k in range(5)))


def selftest(t):
    bad, n = 0, 0

    def check(label, got, want, tol=1e-6):
        nonlocal bad, n
        n += 1
        if isinstance(want, float):
            ok = got is not None and abs(got - want) <= tol
        else:
            ok = got == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {label}: {got} (want {want})")

    # Regulation constants the model rests on.
    for name, want in (('poison', 4.0), ('scarlet_rot', 4.0), ('bleed', 7.0), ('death_blight', 3.0),
                       ('frost', 5.0), ('sleep', 5.0), ('madness', 4.0)):
        check(f'resistRecoverPoint {name} player', t.recover[name], want)
    for image, va in REQ_PENALTY_VA.items():
        v = _read_f32(image, va)
        if v is None:
            print(f'skip {image} not on disk')
            continue
        check(f'0.2 status penalty in {image} @ {va:#x}', round(v, 6), 0.2)

    # Base build-up agrees with er-mechanics-ar, which agrees with the `COMMUNITY` calculator.
    for weapon, aff, lvl, stats, two, expected in AR.SELFTEST_CASES:
        want = {k.split('.')[1]: v for k, v in expected.items() if k.startswith('status.')}
        if not want:
            continue
        ws = weapon_status(t, weapon, aff, lvl, stats, two, pvp=False)
        got = {}
        for s in ws['sources']:
            got[s['status']] = got.get(s['status'], 0) + s['base'] * s['arcane']
        for k, v in want.items():
            check(f'{aff} {weapon} +{lvl} {k} (vs AR reference)', round(got.get(k, 0), 2), round(v, 2), 0.02)

    # stateInfo dispatch: every weapon status row carries exactly the status its stateInfo names.
    stray = 0
    for w in t.ar.weapons.values():
        for k in range(3):
            row = t.sp.get(w.get(f'spEffectBehaviorId{k}', -1))
            if row:
                nz = [s[0] for s in STATUSES if row.get(s[2], 0) > 0]
                if nz and (not row_status(row) or len(nz) > 1):
                    stray += 1
    check('weapon status rows with a value outside their stateInfo', stray, 0)

    # vsPlayer status rates are all 1.0 in this regulation (`MEASURED`): PvP build-up == PvE.
    vs = {round(w.get(f'vsPlayerDmgCorrectRate_{s[4]}', 1.0), 4) for w in t.ar.weapons.values() for s in STATUSES}
    check('distinct vsPlayerDmgCorrectRate status values', sorted(vs), [1.0])

    # Gauge arithmetic (`FUN_14043d8a0`: proc when gauge - build-up < 1).
    check('hits_to_proc(50, 100)', hits_to_proc(50, 100), 2)
    check('hits_to_proc(34, 100)', hits_to_proc(34, 100), 3)
    check('hits_to_proc(50, 101)', hits_to_proc(50, 101), 3)
    check('hits_to_proc(50, 100, 1 s apart, 7/s)', hits_to_proc(50, 100, 1.0, 7.0), 3)

    # Proc HP: Blood Loss 6401 is 15% + 100; Frostbite 107500 raises damage taken to x1.2.
    check('bleed 6401 proc at 2000 HP', proc_effect(t, 6401, 2000.0)['hp_total'], 400.0)
    check('frost 107500 damage taken', round(proc_effect(t, 107500, 2000.0)['damage_taken_mult'], 4), 1.2)

    # Per-attack correction on Uchigatana +25 (45 bleed); grease rows add on both rates.
    ws = weapon_status(t, 'Uchigatana', 'Standard', 25, {'str': 18, 'dex': 40}, False)
    r100, r65 = _plain_row(t, 100), _plain_row(t, 65)
    check('Uchigatana +25 bleed, rate 100', hit_buildup(t, ws, r100).get('bleed'), 45)
    check('Uchigatana +25 bleed, rate 65', hit_buildup(t, ws, r65).get('bleed'), int(45 * 0.65))
    g = t.greases()
    check('blood grease rows found', sorted(k for k in g if 'Blood' in k), ['Blood Grease', 'Drawstring Blood Grease'])
    check('Blood Grease + Uchigatana, rate 100', hit_buildup(t, ws, r100, 'Blood Grease').get('bleed'), 45 + 30)
    wsu = weapon_status(t, 'Uchigatana', 'Standard', 25, {'str': 5, 'dex': 40}, False)
    check('Uchigatana +25 with STR unmet: x0.2', hit_buildup(t, wsu, r100).get('bleed'), 9)

    # Boluses: every status has one, each ends on a -99999 row of that status (regulation).
    check('bolus per status', sorted(t.bolus), sorted(NAMES))
    check('bolus amounts', sorted({b['amount'] for b in t.bolus.values()}), [-99999])
    check('Stanching Boluses -> 3050 -> 3051', t.bolus['bleed']['speffects'], [3050, 3051])
    check('bolus item clip: ConsumeCurrentGoods frame in a000_050000', cure_frame(), 31)
    check('item use after a small / middle / large reaction (TAE)',
          [goods_ready(1), goods_ready(2), goods_ready(3)], [12, 25, 35])

    # Refill between hits: 45 bleed on 332 robustness, one hit per 60 frames vs back to back.
    check('refill between hits raises hits to proc', hits_to_proc(45, 332, 60 / FPS, t.recover['bleed'])
          > hits_to_proc(45, 332), True)
    # Re-proc while active: every status row the model applies is exclusive-category (R1 in
    # `FUN_1404fc690`), one category per status; the 0x2710 threshold is in both images.
    rows_by_status = {}
    for w in t.ar.weapons.values():
        for k in range(3):
            row = t.sp.get(w.get(f'spEffectBehaviorId{k}', -1))
            if row and row_status(row):
                rows_by_status.setdefault(row_status(row)[0], set()).add(row['spCategory'])
    for gid in t.greases().values():
        rows_by_status.setdefault(row_status(t.sp[gid])[0], set()).add(t.sp[gid]['spCategory'])
    check('status rows: one exclusive category (>= 10000) per status',
          all(len(c) == 1 and min(c) >= EXCLUSIVE_CATEGORY_MIN for c in rows_by_status.values())
          and len({min(c) for c in rows_by_status.values()}) == len(rows_by_status), True)
    for image, va in EXCLUSIVE_CATEGORY_VA.items():
        p = REPO / image
        if not p.exists():
            print(f'skip {image} not on disk')
            continue
        with open(p, 'rb') as f:
            f.seek(va - 0x140000000)
            got = f.read(len(EXCLUSIVE_CATEGORY_BYTES))
        check(f'R1 `mov eax, 0x2710; cmp r10w, ax` in {image} @ {va:#x}', got.hex(), EXCLUSIVE_CATEGORY_BYTES.hex())

    # Target gate (section 1b): the +0x16c reads are in both images.
    for image, vas in TARGET_GATE_VA.items():
        p = REPO / image
        if not p.exists():
            print(f'skip {image} not on disk')
            continue
        with open(p, 'rb') as f:
            for key, va in vas.items():
                f.seek(va - 0x140000000)
                got = f.read(len(TARGET_GATE_BYTES[key]))
                check(f'target gate {key} read in {image} @ {va:#x}', got.hex(), TARGET_GATE_BYTES[key].hex())
    # effectTargetPlayer is not a gate: Morgott's Great Rune (620, max HP x1.25 on the player)
    # has every legacy target bit at 0, and so do 3570 rows in all.
    legacy = ('effectTargetSelf', 'effectTargetFriend', 'effectTargetEnemy', 'effectTargetPlayer',
              'effectTargetAI', 'effectTargetLive', 'effectTargetGhost')
    check("Morgott's Great Rune 620: max HP x1.25, every legacy target bit 0",
          (round(t.sp[620]['maxHpRate'], 2), sum(t.sp[620][k] for k in legacy)), (1.25, 0))
    # The frost rows with effectTargetPlayer 0 pass the gate that is applied.
    for rid in (880, 881, 829, 1724, 1800, 6700):
        r = t.sp[rid]
        check(f'frost row {rid}: Player 0, OpposeTarget 1, not refused',
              (r['effectTargetPlayer'], r['effectTargetOpposeTarget'], hostile_target_refusal(r)), (0, 1, None))
    reach = set()
    for a in t.atk.values():
        reach |= {a[f'spEffectId{i}'] for i in range(5) if a[f'spEffectId{i}'] > 0}
    for w in t.ar.weapons.values():
        for k in range(3):
            b = w.get(f'spEffectBehaviorId{k}', -1)
            if b > 0:
                reach |= {b + t.ar.reinforce[w['reinforceTypeId'] + lv].get(f'spEffectId{k + 1}', 0)
                          for lv in range(26) if w['reinforceTypeId'] + lv in t.ar.reinforce}
    reach |= {r['atkOccurrenceSpEffectId'] for r in t.sp.values() if r['atkOccurrenceSpEffectId'] > 0}
    refused = sorted(rid for rid in reach if rid in t.sp and row_status(t.sp[rid])
                     and hostile_target_refusal(t.sp[rid]))
    check('status rows a weapon, buff, grease or AtkParam_Pc reaches that a PvP victim refuses', refused, [])
    check('a row with OpposeTarget 0 adds no build-up',
          _row_amount(t, ws, 880, None, False, 1.0, None) is not None
          and hostile_target_refusal(dict(t.sp[880], effectTargetOpposeTarget=0)) is not None, True)

    # The engagement simulator.
    check('a live proc refuses the next: 1 proc in 3 engagements with a 90 s lockout',
          simulate((400,), (), 300, 4.0, 90.0, False, 3)[0], 1)
    check('...and 3 with a 1 s lockout', simulate((400,), (), 300, 4.0, 1.0, False, 3)[0], 3)
    check('carrier, one 100 hit per engagement against 300: never procs',
          simulate((100,), (), 300, 4.0, 90.0, True, ENGAGEMENT_LIMIT)[2], None)
    check('carrier, a true-combo chain of three 110 hits: procs on hit 3 of engagement 1',
          simulate((110, 110, 110), (20.0, 20.0), 300, 4.0, 90.0, True, ENGAGEMENT_LIMIT)[2], (1, 3))
    check('non-carrier accumulates with 20 points of decay per gap: procs in engagement 4',
          simulate((100,), (), 300, 4.0, 90.0, False, ENGAGEMENT_LIMIT, 5.0)[2], (4, 4))
    check('non-carrier with decay above the build-up per engagement: never procs',
          simulate((100,), (), 300, 30.0, 90.0, False, ENGAGEMENT_LIMIT, 5.0)[2], None)
    check('a 90 s DoT proc on the first hit of a 20 s fight credits 21 ticks',
          simulate((400,), (), 300, 4.0, 90.0, False, 5, 5.0, 20.0, True, 90, 1.0)[:2], (1, 21))
    check('a carrier cures 2 s after each engagement: 5 procs of 3 ticks, the last cut to 1 by the fight end',
          simulate((400,), (), 300, 4.0, 90.0, True, 5, 5.0, 20.0, True, 90, 1.0, 2.0)[:2], (5, 13))
    check('engagement lengths of a chain landing 1.0 then 0.5',
          engagement_lengths([{'p': 1.0}, {'p': 0.5}]), [(2, 0.5), (3, 0.5)])
    check('combo_land: true when intact, no when broken, half staggered',
          combo_land({'on_intact': {'verdict': 'true'}, 'on_break': {'verdict': 'no'}}, 0.5), 0.5)
    # No bolus fits inside a true combo: the roll opens before any cure could land.
    check(f'gap 20 < cure after an unflinching hit ({cure_ready(0)} f): no opportunity',
          cure_opportunity(20, 0), 0.0)
    check('gap 20 after a small stagger: none', cure_opportunity(20, 1), 0.0)

    rows = corpus_rows(rl=150)
    base = Defenders(t, rows)
    check(f'fight length from the corpus median HP {base.median_hp} / {FIGHT_REF_DAMAGE}',
          base.fight_engagements, math.ceil(base.median_hp / FIGHT_REF_DAMAGE))
    unc = {'atk_row': r100, 'hit_windows': [(10, 12)]}
    three = [{'attack': unc, 'p': 1.0, 'gap': 20.0}] * 2
    single = status_expected(t, ws, unc, base)['bleed']
    combo3 = status_expected(t, ws, unc, base, chain=three)['bleed']
    check('Uchigatana single hits: no carrier ever procs from one 45 hit',
          single['proc_share']['carrier'], 0.0)
    check('three-hit true combos proc more per landed hit than single hits',
          combo3['hp_per_hit'] > single['hp_per_hit'], True)
    slow = status_expected(t, ws, unc, base, chain=three, eng_s=10.0)['bleed']
    check('more time between engagements: more engagements to proc for a non-carrier',
          slow['engagements_to_proc']['non_carrier'] > combo3['engagements_to_proc']['non_carrier'], True)

    # A resistance talisman lowers the value.
    add = talisman_resist(['Stalwart Horn Charm +2'])
    check('Stalwart Horn Charm +2: bleed +180, frost +180', (add['bleed'], add['frost'], add['poison']),
          (180, 180, 0))
    charm = status_expected(t, ws, unc, Defenders(t, rows, add), chain=three)['bleed']
    check('the charm lowers expected bleed HP per hit', charm['hp_per_hit'] < combo3['hp_per_hit'], True)

    # A poison / rot proc is cut short by a carried bolus and by the end of the fight.
    rot = {'resist': {g: 300 for g in GROUPS}, 'hp': 2000.0, 'tools': {'Preserving Boluses'}}
    bare = dict(rot, tools=set())
    rot_ws = weapon_status(t, 'Rotten Greataxe', 'Standard', 10, {'str': 40, 'dex': 20}, True)
    rot_chain = [{'attack': unc, 'p': 1.0, 'gap': 20.0}] * 5
    carried = status_expected(t, rot_ws, unc, Defenders(t, [rot]), chain=rot_chain)['scarlet_rot']
    none = status_expected(t, rot_ws, unc, Defenders(t, [bare]), chain=rot_chain)['scarlet_rot']
    long = status_expected(t, rot_ws, unc, Defenders(t, [bare]), chain=rot_chain, engagements=18)['scarlet_rot']
    check('rot proc is a DoT locked out for its 90 s', (carried['dot'], carried['lockout']), (True, 90.0))
    check('Preserving Boluses cut the credited rot', carried['proc_hp_credited'] < none['proc_hp_credited'], True)
    check('a short fight cuts the credited rot', none['proc_hp_credited'] < none['proc_hp'], True)
    check('a longer fight credits more of it', long['proc_hp_credited'] > none['proc_hp_credited'], True)
    # A schedule (one count per fight point) pools every point's procs over every point's hits.
    short = status_expected(t, rot_ws, unc, Defenders(t, [bare]), chain=rot_chain, engagements=5)['scarlet_rot']
    mix = status_expected(t, rot_ws, unc, Defenders(t, [bare]), chain=rot_chain,
                          engagements=(5, 18, 18, 18))['scarlet_rot']
    pooled = (0.25 * 5 * short['hp_per_hit'] + 0.75 * 18 * long['hp_per_hit']) / (0.25 * 5 + 0.75 * 18)
    check('a schedule 5 / 18 / 18 / 18: pooled HP per hit, mean engagements 14.75',
          (round(mix['hp_per_hit'], 6), mix['fight_engagements']), (round(pooled, 6), 14.75))
    check('a whole 90-tick DoT, then a new proc only once it has expired (t = 90 s)',
          simulate((400,), (), 300, 4.0, 90.0, False, 19, 5.0, 93.0, True, 90, 1.0)[:2], (2, 94))
    print(f'{n - bad}/{n} passed')
    return bad == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('weapon', nargs='?')
    ap.add_argument('--affinity', default='Standard')
    ap.add_argument('--level', type=int, default=25)
    ap.add_argument('--stats', default='')
    ap.add_argument('--two-handed', action='store_true')
    ap.add_argument('--pve', action='store_true', help='drop the vsPlayer rates')
    ap.add_argument('--grease', help='a grease name, e.g. "Drawstring Blood Grease"')
    ap.add_argument('--slot', default='r1_1')
    ap.add_argument('--all-slots', action='store_true')
    ap.add_argument('--interval', type=float, help='seconds between hits (gauge refill)')
    ap.add_argument('--rl', type=int, default=150)
    ap.add_argument('--window', type=int, default=10)
    ap.add_argument('--corpus', action='store_true')
    ap.add_argument('--top', action='store_true')
    ap.add_argument('--top-n', type=int, default=25)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.corpus:
        show_corpus(a)
        return 0
    t = Tables()
    if a.grease and a.grease not in t.greases():
        ap.error(f'unknown grease; one of {sorted(t.greases())}')
    if a.selftest:
        return 0 if selftest(t) else 1
    if a.top:
        top_weapons(t, a)
        return 0
    if not a.weapon:
        ap.error('weapon is required unless --selftest, --corpus or --top')
    show_weapon(t, a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
