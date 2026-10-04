"""Hit-path readers of attacker state, from static RE of the 1.16.2 executable (Ghidra :8765, shift 0).

Each entry says what the damage path reads off the attacker when a bullet hits, whether that read
is live (at the hit) or a launch-time snapshot, the gate, and how sure the link is. The scan
crosses these with the regulation's carriers; the table is the one place the RE lives.

Labels: `VERIFIED` = read in the decompile/disassembly this session or in a cited doc;
`INFERRED` = follows from verified pieces, not traced end to end.
"""

# Launch side (snapshot), for contrast. FUN_14038e380 builds a bullet's AttackInfo at creation:
# weaponParamId for both hands from the spawn's equip slots, IsTwoHanding, and
# FUN_1404f4520(owner->specialEffect) -- the SpEffect *AttackPowerRate / *AttackRate products
# and the flat element adds (grease fire 85 etc.) -- all frozen into the bullet (call 0x14038e4ed,
# `VERIFIED`). HitBulletID children copy the parent's AttackInfo (memory
# bullets-carry-firing-weapon-hit-speffects-great-stars-heal-2026-10-01). So element grease and
# talisman AttackRate on a bullet are what was live at launch.

READERS = {
    'R1': {
        'title': 'weapon-buff on-hit SpEffect (stateInfo 152/153)',
        'reads': "the first live attacker SpEffect entry with stateInfo 152/153 that passes the hit "
                 "context; its atkOccurrenceSpEffectId is applied to the victim",
        'where': 'CalculateDamage2 0x1404483b0, call 0x140448dd5 -> FUN_1404f71e0(dealer->specialEffect, '
                 'FUN_1404fee60(ADI)) -> IsApplicableForCategory 0x140500930 -> FUN_1403e8c90(victim, id, dealer)',
        'live': 'live: the entry list is walked per hit',
        'gate': 'hit context byte ADI+0xda (bullet: BehaviorParam.category / goods / magic spEffectCategory), '
                'sub-category mask, not a throw row; entry flags & 0x800c0003 == 0',
        'evidence': 'VERIFIED',
        'needs_damage': False,
    },
    'R5': {
        'title': 'arm style read inside the context-12 gate',
        'reads': "attacker->GetArmStyle() at the hit; a left-hand (wepParamChange 2) buff passes "
                 "context 12 only while it returns 2 (left weapon two-handed)",
        'where': 'IsApplicableForCategory 0x140500930, case Weapon (12): param_3 = SpecialEffect.owner, '
                 'vtable GetArmStyle',
        'live': 'live: the owner pointer is the attacker at the hit',
        'gate': 'context 12 only; then the same first-entry pick as R1 (a right-hand buff, if live, '
                'competes -- which comes first in the list is not traced)',
        'evidence': 'VERIFIED',
        'needs_damage': False,
    },
    'R2': {
        'title': 'atkEnemy/atkPlayer DmgCorrectRate',
        'reads': "product of atkEnemyDmgCorrectRate_* (PvE) or atkPlayerDmgCorrectRate_* (PvP) over the "
                 "bullet OWNER's live SpEffect entries",
        'where': 'AttackDamageInfo::CalculateDamageCorrections 0x140684d70: attacker FieldIns type 3 '
                 '(bullet) -> CSBulletIns.owner -> GetChrInsFromHandle -> CalculateAtkEnemyDmgCorrectRates '
                 '0x1404f4390 / CalculateAtkPlayerDmgCorrectRates 0x1404f5080',
        'live': 'live: resolved from the bullet handle at the hit',
        'gate': 'entry flags & 0x800c0003 == 0 and IsApplicableForCategory with the hit context',
        'evidence': 'VERIFIED',
        'needs_damage': True,
    },
    'R3': {
        'title': 'attack rating from the owner (stats, durability, equip load)',
        'reads': "owner's PlayerGameData (attributes), the durability of the equip slot the bullet "
                 "was fired from, and equip weight for Blue Dancer (stateInfo 315/316); weapon id, "
                 "flat adds and AttackPowerRate stay the launch snapshot",
        'where': 'FUN_140d24b10 -> FUN_140683020: FieldIns type 3 -> bullet+0x4c0 owner handle -> owner '
                 'vtable +0x358 = FUN_140651d40 (players) -> FUN_1406832a0(PlayerGameData, durability, '
                 'AttackInfo, specialEffect)',
        'live': 'code path VERIFIED; that DmgMan builds the record at contact rather than when the '
                'bullet registers its hit entry is INFERRED',
        'gate': 'any damaging bullet whose AtkParam has a correction (motion value)',
        'evidence': 'VERIFIED code / INFERRED timing',
        'needs_damage': True,
    },
    'R4': {
        'title': 'Spear Talisman counter boost (stateInfo 197)',
        'reads': "attacker's live stateInfo 197 entries' *AttackRate, multiplied in when the victim's "
                 "counter-hit rate is above 1",
        'where': 'CSChrDamageModule::CalculateDamage 0x1404472b0, call 0x140447b61 -> FUN_1404f5310(dealer->specialEffect)',
        'live': 'live',
        'gate': 'no category gate (VERIFIED); victim must be in a counter window (stateInfo 110)',
        'evidence': 'VERIFIED',
        'needs_damage': True,
    },
    'R6': {
        'title': 'attacker-targeted hit SpEffects land on the live attacker',
        'reads': "the hit's SpEffect slots whose row has effectTargetAttacker are applied to the "
                 "dealer at the hit, not at launch",
        'where': 'CalculateDamage2 slot loop (13 slots, 0x1404489eb): row +0x160 bit 1 -> FUN_1403e8b70(dealer, id)',
        'live': 'live target; the slot rows themselves are the launch snapshot',
        'gate': 'per landed hit; slots 0-4 are AtkParam.spEffectId0..4 (VERIFIED); that bullet '
                'spEffectId0..4 fill later slots is INFERRED',
        'evidence': 'VERIFIED (AtkParam slots) / INFERRED (bullet slots)',
        'needs_damage': False,
    },
    'S1': {
        'title': 'launch-snapshot flat element adds on a zero-damage bullet',
        'reads': "flat SpEffect element adds frozen into the AttackInfo at launch "
                 "(FUN_1404f4520), added after every multiplier in FUN_1406832a0, so a zero-motion-"
                 "value bullet would still carry them",
        'where': 'FUN_14038e380 -> FUN_1404f4520 (0x14038e4ed); FUN_1406832a0 "+ AttackInfo+0x48..+0x68" '
                 'unconditional ADDSS (grease.md section 4)',
        'live': 'snapshot at launch (the opposite of R1: must be live BEFORE firing)',
        'gate': 'launch context accepts the row; whether a zero-damage AtkParam (IsZeroDamage) skips '
                'the record is not traced',
        'evidence': 'INFERRED',
        'needs_damage': False,
    },
}

# Readers found on the hit path that have no player-switchable source; listed in the doc, not crossed.
UNCROSSED = [
    ('MP level sync', 'CalculateDamage: SpecialEffect::GetMPLevelCorrection(dealer->specialEffect, '
     'weapon levelSyncCorrectId)', 'live; multiplayer level sync only', 'VERIFIED'),
    ('dealer stateInfo 157', 'CalculateDamage 0x1404472b0: HasSpecialEffectWithStateInfo(dealer, 0x9d) '
     'forces ADI+0x114 bit 7 on', 'live; 157 rows are debuffs other characters put on you (buffs.md)', 'VERIFIED'),
    ('atkFlickPower', 'FUN_140447180 (from CalculateDamage, unless ADI+0x267 & 2): '
     'GetAtkFlickPower(dealer->specialEffect)', 'live; guard-repel strength', 'VERIFIED'),
]

KNOWN = {
    ('R1', 20003309): 'Piquebone smoke (rain-of-arrows-seppuku.md)',
    ('R1', 2416): 'Poisonous Mist cloud (rain-of-arrows-seppuku.md)',
    ('R1', 2411): 'Chilling Mist cloud (rain-of-arrows-seppuku.md)',
    ('R1', 2019): 'Eruption (rain-of-arrows-seppuku.md)',
}
