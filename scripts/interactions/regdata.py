#!/usr/bin/env python3
"""Regulation tables and the hit-context gate shared by the interaction scan.

Moved from the buff-carrier helper written for docs/er-mechanics/rain-of-arrows-seppuku.md.

The gate (1.16.2, Ghidra :8765, shift 0):

    FUN_14038e210 picks a bullet launch's context byte:
        spawn+0x14 (explicit bullet id) == -1 -> BehaviorParam.category (+0x1c)
        else goodsId (+0x18) != -1          -> EquipParamGoods.spEffectCategory (+0x40)
        else magicId (+0xc) != -1           -> Magic.spEffectCategory (+0x28)
    HitBulletID children copy the parent's attack info (FUN_14039bba0 -> FUN_14038b740).
    SpecialEffectEntry::IsApplicableForCategory 0x140500930, by context:
        ctx 1   refuse wepParamChange 2,3,4         (right hand and hand-neutral)
        ctx 2   refuse wepParamChange 1,3,4         (left hand)
        ctx 3   magParamChange                      ctx 4  miracleParamChange
        ctx 10  shamanParamChange                   ctx 11 refuse 3,4 (both hands)
        ctx 12  1 always; 0/5/6; 2 only while owner->GetArmStyle() == 2 (left two-handed), read
                at the hit from the live attacker
        ctx 9   0/5/6 or 4 (kick)                   ctx 0,5-8,>12  0/5/6 only
    then CheckMagicSubCategoryChangeMask 0x140d50880 (an all-zero row mask passes; otherwise the
    row mask and the hit mask must be non-zero), and a row with throwAttackParamChange set passes
    only on a throw hit.

A second gate decides how much status the buff's on-hit row builds (`status_reaches`):

    FUN_140d24b10 writes ADI+0x13c = AtkParam.statusAilmentAtkPowerCorrectRate * 0.01 and
    ADI+0x140 = statusAilmentAtkPowerCorrectRate_byPoint * 0.01.
    CalculateDamage2, after FUN_1404f71e0 returns the on-hit id (0x140448e12): if the on-hit row's
    byte +0x259 bit 0 (isUseStatusAilmentAtkPowerCorrect) is set, the buildup rate handed to
    FUN_1403e8c90 -> FUN_1403fade0 -> FUN_14043daf0 is ADI+0x140 * ADI+0x13c * rate. 48 of the 54
    weapon-buff on-hit rows set that bit (every grease, Seppuku, the mist skills' 882/880), so a
    bullet whose AtkParam has either rate at 0 applies the row with zero buildup.
"""
import importlib.util
import os

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_s = importlib.util.spec_from_file_location('er_param_read', os.path.join(SCRIPTS, 'er-param-read.py'))
PR = importlib.util.module_from_spec(_s)
_s.loader.exec_module(PR)

STATUS = ('poizonAttackPower', 'diseaseAttackPower', 'bloodAttackPower', 'curseAttackPower',
          'freezeAttackPower', 'sleepAttackPower', 'madnessAttackPower')
ELEMENT_ADDS = ('physicsAttackPower', 'magicAttackPower', 'fireAttackPower',
                'thunderAttackPower', 'darkAttackPower')
ENEMY_CORR = tuple(f'atkEnemyDmgCorrectRate_{e}' for e in ('Physics', 'Magic', 'Fire', 'Thunder', 'Dark'))
PLAYER_CORR = tuple(f'atkPlayerDmgCorrectRate_{e}' for e in ('Physics', 'Magic', 'Fire', 'Thunder', 'Dark'))
SP_LINKS = ('cycleOccurrenceSpEffectId', 'replaceSpEffectId', 'accumuOverFireId', 'accumuUnderFireId')


def accepts(ctx, sp, left_two_handed=False):
    """IsApplicableForCategory's context switch (0x140500930), before the sub-category mask."""
    w = sp['wepParamChange']
    if ctx == 1:
        return w not in (2, 3, 4)
    if ctx == 2:
        return w not in (1, 3, 4)
    if ctx == 3:
        return bool(sp['magParamChange'])
    if ctx == 4:
        return bool(sp['miracleParamChange'])
    if ctx == 10:
        return bool(sp['shamanParamChange'])
    if ctx == 11:
        return w not in (3, 4)
    w056 = w in (0, 5, 6)
    if ctx == 12:
        return w == 1 or w056 or (w == 2 and left_two_handed)
    if ctx == 9:
        return w056 or w == 4
    return w056


def mask_passes(sp, atk):
    """CheckMagicSubCategoryChangeMask, assuming the hit mask is the AtkParam subCategory1..4 set.

    `VERIFIED`: an all-zero row mask passes (compare against 0x143d67a08, then a bitwise and). `INFERRED`: that
    the row mask bits are magicSubCategoryChange1..3 and the hit bits AtkParam subCategory1..4.
    """
    row = {sp[f'magicSubCategoryChange{i}'] for i in (1, 2, 3)} - {0}
    if not row:
        return True
    if atk is None:
        return False
    return bool(row & ({atk[f'subCategory{i}'] for i in (1, 2, 3, 4)} - {0}))


def status_scale(atk):
    """ADI+0x140 * ADI+0x13c for a hit with AtkParam row `atk` (both are percent / 100)."""
    if atk is None:
        return 0.0
    return (atk['statusAilmentAtkPowerCorrectRate'] / 100.0
            * atk['statusAilmentAtkPowerCorrectRate_byPoint'] / 100.0)


def status_reaches(on_hit, atk):
    """Whether a weapon buff's on-hit row builds any status through a hit with AtkParam `atk`.

    `on_hit` is the atkOccurrenceSpEffectId row. A row without isUseStatusAilmentAtkPowerCorrect
    is not scaled; one with it is scaled by `status_scale(atk)` (CalculateDamage2 0x140448e12).
    """
    if on_hit is None:
        return False
    if not on_hit['isUseStatusAilmentAtkPowerCorrect']:
        return True
    return status_scale(atk) > 0


def gate(ctx, sp, atk, left_two_handed=False):
    """The full live-entry filter for a non-throw bullet hit."""
    if sp.get('throwAttackParamChange'):
        return False
    return accepts(ctx, sp, left_two_handed) and mask_passes(sp, atk)


class Reg:
    def __init__(self):
        f = PR.load()

        def table(stem):
            return {r['id']: r for r in PR.rows(PR.param_bytes(f, stem))[0]}
        self.beh = table('BehaviorParam_PC')
        self.bullet = table('Bullet')
        self.atk = table('AtkParam_Pc')
        self.sp = table('SpEffectParam')
        self.goods = table('EquipParamGoods')
        self.magic = table('Magic')
        self.weapon = table('EquipParamWeapon')
        self.accessory = table('EquipParamAccessory')
        self.protector = table('EquipParamProtector')
        self.names = {s: PR.row_names(s) for s in ('BehaviorParam_PC', 'Bullet', 'SpEffectParam',
                                                    'EquipParamGoods', 'EquipParamWeapon',
                                                    'AtkParam_Pc', 'EquipParamAccessory',
                                                    'EquipParamProtector')}

    def name(self, stem, i):
        return (self.names[stem].get(i) or '').strip()

    def status_of(self, sid):
        s = self.sp.get(sid)
        if not s:
            return {}
        return {k: s[k] for k in STATUS if s[k]}

    def chain(self, root, fast=5.0):
        """Bullets reachable from `root` through HitBulletID and intervalCreateBulletId.

        A projectile faster than `fast` m/s counts at most 0.5 s of its life: its life is
        mostly flight to the first contact, not time spent lingering.

        Each node carries `end`: how long after launch it can still be hitting, on the longest
        path. A HitBulletID child is taken to spawn when its parent starts (the parent's flight to
        its contact is not modelled, so an arrow's 4 s life does not count as linger time); an
        interval child can spawn as late as its emitter's end.
        """
        best = {}
        todo = [(root, 'root', 0.0, (root,))]
        while todo:
            bid, how, start, path = todo.pop()
            b = self.bullet.get(bid)
            if b is None:
                continue
            life = max(b['life'], 0.0)
            if b['initVellocity'] > fast:
                life = min(life, 0.5)
            end = start + life
            if bid in best and best[bid]['end'] >= end:
                continue
            best[bid] = {'id': bid, 'how': how, 'end': end, 'path': path}
            if b['HitBulletID'] > 0 and b['HitBulletID'] not in path:
                todo.append((b['HitBulletID'], 'HitBulletID', start, path + (b['HitBulletID'],)))
            c = b['intervalCreateBulletId']
            if c > 0 and c not in path:
                todo.append((c, 'intervalCreateBulletId', end, path + (c,)))
        return list(best.values())

    def atk_info(self, aid):
        a = self.atk.get(aid)
        if a is None:
            return None
        dmg = sum(a[k] for k in ('atkPhys', 'atkMag', 'atkFire', 'atkThun', 'atkDark'))
        cor = sum(a[k] for k in ('atkPhysCorrection', 'atkMagCorrection', 'atkFireCorrection',
                                 'atkThunCorrection', 'atkDarkCorrection'))
        return {'damaging': dmg > 0 or cor > 0, 'oppose': a['opposeTarget'],
                'flat': dmg, 'corr': cor, 'row': a}

    def bullet_info(self, bid):
        b = self.bullet[bid]
        a = self.atk_info(b['atkId_Bullet']) if b['atkId_Bullet'] >= 0 else None
        return {'id': bid, 'name': self.name('Bullet', bid), 'life': b['life'],
                'r': b['hitRadius'], 'rmax': b['hitRadiusMax'], 'speed': b['initVellocity'],
                'rec': b['dmgHitRecordLifeTime'], 'endless': b['isEndlessHit'],
                'shared': b['isUseSharedHitList'], 'both': b['isHitBothTeam'],
                'pen': b['isPenetrateChr'], 'atk': b['atkId_Bullet'], 'a': a,
                'sp': [b[f'spEffectId{i}'] for i in range(5) if b[f'spEffectId{i}'] > 0],
                'limit': b['createLimitGroupId']}

    def sp_closure(self, roots):
        """SpEffect ids reachable from `roots` through the self-side link fields."""
        seen, todo = set(), [r for r in roots if r and r > 0]
        while todo:
            i = todo.pop()
            if i in seen or i not in self.sp:
                continue
            seen.add(i)
            s = self.sp[i]
            for f in SP_LINKS:
                if s[f] > 0:
                    todo.append(s[f])
        return seen
