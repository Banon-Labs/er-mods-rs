#!/usr/bin/env python3
"""List a weapon's attacks with motion value, poise damage, stamina cost and hit count.

Reads the installed regulation.bin through `scripts/er-param-read.py` and, when the
unpacked player TimeAct is on disk, the weapon's `a<wepmotionCategory>.tae` for hit
windows, hit counts and hyperarmor windows. Write-up: `docs/er-mechanics/attacks.md`.

Labels follow that document: `VERIFIED` = regulation value or read out of the 1.16.2
executable (addresses below), `TAE` = decoded animation event, `INFERRED` = fits the
data but the consumer was not traced, `COMMUNITY` = outside claim.

The chain (`VERIFIED`, `PlayerIns::ResolveBehaviorId` 0x140652280):

    judge   = TAE event type 1 (AttackBehavior 0x1404266d0) Args[2]
    kind    = max(1, judge // 1000)
    row     = (kind * 100000 + EquipParamWeapon.behaviorVariationId) * 1000 + judge % 1000
    missing -> (variation // 100 + kind * 1000) * 100000 + judge % 1000   (family row)
    missing and judge >= 1000 -> kind * 100000000 + judge % 1000
    BehaviorParam_PC[row].refType 0 -> AtkParam_Pc[refId]

Per-hit numbers (`VERIFIED`, 1.16.2 addresses):

    physical   = (attackBasePhysics * physicsAtkRate) * atkPhysCorrection / 100
                 [+ atkPhys if isAddBaseAtk], then times the stat-scaling factor
                 (0x1406832a0; the scaling factor is FUN_140690390)
    poise      = saWeaponDamage * saWeaponAtkRate * atkSuperArmorCorrection / 100
                 [+ atkSuperArmor if isAddBaseAtk], times durability and SpEffect (0x14068af30)
    stamina    = int(BehaviorParam_PC.stamina * staminaConsumptionRate * SpEffect rate
                 * lowStatus rate) charged when the hitbox is created
                 (0x1404428f0 -> 0x140651e90 -> 0x140684430)
    stam. dmg  = attackBaseStamina * staminaAtkRate * atkStamCorrection / 100
                 [+ atkStam if isAddBaseAtk], times SpEffect (0x14068abd6..0x14068ac34)
    guard lvl  = attackBaseRepel * guardAtkRateCorrection / 100 [+ guardAtkRate]
                 + clamp(STR - overStrength, 0, 10) + hand term (0x14068c080)

Hyperarmor (`VERIFIED` handler, `TAE` windows): event 795 (0x14042c2e0) turns the
toughness module on with ToughnessParam row = arg byte 0, damageRatio = arg f32 at +4,
and the weapon's `toughnessCorrectRate`. Max toughness is then
`100 * (sum(armor toughnessCorrectRate) * proCorrectionRate + correctionRate * weapon
toughnessCorrectRate)` (0x140487d10, constant 100.0 at 0x142bb55d8).

Recovery (`VERIFIED` consumer, `TAE` windows). TAE event type 0 (`_ChrActionFlag`
0x1404275e0) switches on Args[0], the JumpTable id. Input ids set
`CSChrActionRequestModule::possibleActionInputs` (`SetPossibleInputState` 0x140407b80),
cancel ids set `possibleActionCancels` (`SetAllowedCancelToActionState` 0x140407af0), and
`CancelMovement` 0x140407c00 sets `taeCancels` bit 2. Both masks are cleared every frame in
`UpdateFromManipulator` 0x140407c60, which queues a new press only while its input bit is set
and marks it ready only while its cancel bit is set; `ActionRequest` 0x140407400 (HKS env
`ActionRequest`) returns that ready bit. So an action can start on the first frame where one
of its input windows and one of its cancel windows overlap:

    R1     input 1 or 87, cancel 4 or 115        R2    input 1 or 87, cancel 4 or 116
    dodge  input 25 or 87, cancel 26              guard input 21 or 87, cancel 22
    move   cancel 11 or 78 (no input gate; `MovementRequest` 0x1404078b0)

TAE event 300 (`ActivateChrActionFlagEarly` 0x140425ba0) opens the same ids early: while
`WeightAtEventStart + (WeightAtEventEnd - WeightAtEventStart) * progress` exceeds the value
picked by its second field (`GetJuptableEarlyActivateValue` 0x14042f950: 0 -> 0.0,
2 -> EquipParamWeapon.weaponWeightRate). Types 1, 3, 4, 5 read runtime state (equip load,
damage module, casting speed) and are left out. Animation length is read from the clip's
`hkaSplineCompressedAnimation` in the unpacked `c0000_a*x.anibnd` shards.

Usage:

    python3 scripts/er-mechanics-attacks.py Dagger
    python3 scripts/er-mechanics-attacks.py 23110000 --grip both --json
    python3 scripts/er-mechanics-attacks.py --selftest
"""
import argparse
import importlib.util
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PR = _load('er_param_read', 'er-param-read.py')

#: Unpacked player TimeAct (`c0000.anibnd.dcx` -> `tae/a<cat>.tae`). Optional input:
#: without it the regulation columns are still printed and TAE columns are empty.
PLAYER_TAE_DIR = os.environ.get(
    'ER_PLAYER_TAE_DIR',
    os.path.expanduser('~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr/'
                       'c0000-anibnd-dcx/INTERROOT_win64/chr/c0000/tae'))
#: Root holding the unpacked player animation binders (`c0000_a2x-anibnd-dcx/...hkx`).
#: Optional input: without it the animation length column is empty.
PLAYER_HKX_ROOT = os.environ.get(
    'ER_PLAYER_HKX_ROOT',
    os.path.expanduser('~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/chr'))
#: WitchyBND's ER TimeAct template, used only as a `COMMUNITY` cross-check of id names.
TAE_TEMPLATE_ER = os.environ.get(
    'ER_TAE_TEMPLATE_ER',
    os.path.expanduser('~/projects/WitchyBND/WitchyBND/Assets/Templates/TAE.Template.ER.xml'))
#: The 1.16.2 flat image the EXE constants were read from (file offset == RVA).
DEOBF_1162 = os.environ.get('ER_DEOBF_1162', os.path.join(ROOT, 'eldenring-deobf.bin'))
#: The installed build's flat image (1.17.1), for checks of code that must hold on it.
DEOBF_1171 = os.environ.get('ER_DEOBF_1171', os.path.join(ROOT, 'eldenring-deobf-1.17.1.bin'))

#: Physical damage type. `AtkParam.atkAttribute` is resolved by 0x140685a90 (1.17.1
#: 0x1406868e0, `VERIFIED` in both images): 253 (`cmp cl, 0xfd`) takes the weapon's
#: `EquipParamWeapon.atkAttribute` (row +0x104), 252 (`0xfc`) its `atkAttribute2` (+0x191); any
#: other value stands. The caller 0x140d24b10 stores the result at `AttackDamageInfo+0x25`
#: (`damageType`), which `CalculateAbsorptions` 0x140689c80 and the SpEffect cut rates read:
#: 0 slash, 1 strike, 2 pierce, 3 standard (neutral); anything else, such as 254, meets no
#: physical absorption at all. A weapon buff can only replace it with 255 (SpEffect
#: `atkAttribute == 0xff`, 0x1404f42e0), and no SpEffect row carries 255, so greases, buffs and
#: affinities never change it (every affinity row repeats the base weapon's pair).
PHYS_TYPE_NAMES = {0: 'slash', 1: 'strike', 2: 'pierce', 3: 'standard'}
WEAPON_ATTRIBUTE_REF = {253: 'atkAttribute', 252: 'atkAttribute2'}
#: `SpEffectParam.stateInfo` that `SpecialEffect::CalculateDefenseModifiers` 0x1404f53e0 (1.17.1
#: 0x1404f61b0) buckets as the counter-hit factor on the defender (`cmp ax, 0x6e`, `VERIFIED`).
COUNTER_STATE_INFO = 110
#: Attacker SpEffects with this state info scale that factor when it is above 1: 0x1404f5310
#: (1.17.1 0x1404f60e0) multiplies it by `physicsAttackRate` and the element `*AttackRate`
#: (`VERIFIED`). Spear Talisman (SpEffect 320600) is the only row, physics 1.15.
COUNTER_BOOST_STATE_INFO = 197
#: TAE event 66: `CSChrTaeAnimEvent::AddSpEffect` 0x14042bfd0 applies SpEffect Args[0] to the
#: animating character (`VERIFIED` decompile; dispatch from `scripts/er-tae-dispatch-decode.py`).
TAE_ADD_SPEFFECT = 66


def resolve_phys_type(atk_attribute, weapon):
    """The physical damage type name an attack's hit carries (see `PHYS_TYPE_NAMES`)."""
    if atk_attribute in WEAPON_ATTRIBUTE_REF:
        atk_attribute = weapon[WEAPON_ATTRIBUTE_REF[atk_attribute]]
    return PHYS_TYPE_NAMES.get(atk_attribute, 'none')

#: TAE event types used here (dispatch decoded by `scripts/er-tae-dispatch-decode.py`).
TAE_JUMP_TABLE = 0          # 0x1404275e0 `_ChrActionFlag`, Args[0] = JumpTable id
TAE_ATTACK_BEHAVIOR = 1     # 0x1404266d0, Args[2] = behavior judge id
TAE_JUMP_TABLE_EARLY = 300  # 0x140425ba0 `ActivateChrActionFlagEarly`
TAE_TOUGHNESS = 795         # 0x14042c2e0, hyperarmor window
#: Type-0 Args+0xe: a SpEffect state-info id; nonzero skips the event unless the character
#: has it (`VERIFIED`, top of 0x1404275e0). No cancel event in the weapon TAEs sets it.
JUMP_TABLE_STATE_GATE_OFFSET = 0xe
#: Type-1 Args+0xe: the same kind of gate on a hitbox. `AttackBehavior` 0x1404266d0 skips the
#: event unless the u16 there is 0 or the attacker has a SpEffect with that stateInfo
#: (`VERIFIED`, 1.16.2). 189 of the 8930 type-1 events in the 639 player TAEs are gated (a207
#: 103, a037 76, a031 8, a788 2), all on stateInfo 187, which only SpEffect 1908 carries and
#: nothing known applies, so gated hitboxes are left out (this drops the Lance / Messmer
#: Soldier's Spear judge 5000-5005 extras).
ATTACK_STATE_GATE_OFFSET = 0xe
#: Type-1 Args[1]: the attack index, a slot in `CSChrDamageModule`'s 0x10-byte table.
ATTACK_INDEX_OFFSET = 4

#: How often one swing can hit the same target (`VERIFIED`, 1.16.2):
#:
#: - A target is hit once per hit record. `FUN_140523710` (per contact) returns early when the
#:   victim's handle is in the attack's record at +0x278 (or the bullet-shared one at +0x280,
#:   `FUN_14051c5d0`), and after a hit `FUN_140524910` adds it to the record of the root
#:   attack and every attack linked under it (+0x290 parent, +0x2a0 child, +0x298 sibling).
#:   The linked attacks are the hitbox shapes one `FUN_140526430` call creates for one
#:   AtkParam row (`FUN_140525ef0` / `FUN_1405273f0` -> `FUN_14051ec90`), so all shapes of one
#:   row share one record. `FUN_140523540` creates the record with lifetime 0.0
#:   (`xorps xmm2, xmm2` at 0x140523638), so it never expires while the attack lives.
#: - An attack lives in one attack index. `FUN_1404428f0` keeps the index's attack only when
#:   its behavior id (`AttackDamageInfo+0x38`, `cmp [rax+0x38], esi` at 0x1404429f6) equals the
#:   event's; otherwise it removes it (`FUN_140527140`), writes -1 to the handle (0x140442a6f)
#:   and creates a new attack from -1, so the new one starts with an empty record. An index
#:   already claimed this frame (`cmp byte [rbx+4], 0` at 0x14044299d) drops the event.
#: - `FUN_140445d30`, run from the per-character update `FUN_140401d80`, removes every index
#:   no event claimed that frame and clears the claim bytes. So a gap of one frame ends the
#:   record even when the same judge fires again afterwards.
#: - Records of different indices are separate, and nothing on the victim side (the team and
#:   immunity checks `FUN_1404443e0` / `IsImmuneToAttack` 0x1403f3b90, the stagger write
#:   `FUN_140445b20`) remembers an earlier hit from the same attacker. Pooled attacks release
#:   their records on retirement (`FUN_14051eef0`).
#:
#: Consequence: one hit record per run of consecutive frames in which one index is held by one
#: behavior id. A different judge taking over an index, a gap, or another index each give the
#: target one more possible hit. Whether that hit lands is geometry: the hitbox must still
#: touch the target, which this tool does not model, so the count is a ceiling (`max_hits`).
#:
#: `sweep_hits` also drops a record that takes an index over directly from a different judge
#: (`follows_judge` set). `INFERRED`: such an event is the next segment of the same blade
#: sweep (Milady splits nearly every swing this way, 120 after 123 on index 0), so a target the
#: earlier segment hit is taken to be behind the blade by then. A runtime count of
#: `FUN_140524910` calls per swing against a stationary target would prove or break it.
#:
#: The team gate is `getTeamTypeRelationshipWithAtkParam` 0x14051a980 ->
#: `canTeamTypeHitAnother`, fed AtkParam `opposeTarget` / `friendlyTarget` / `selfTarget`.
#: That a row with all three 0 hits nobody is `INFERRED`: the per-team-pair
#: `CSTeamTypeRelation::Validate` implementations were not read.
HIT_RECORD_NOTE = 'VERIFIED 1.16.2: FUN_1404428f0, FUN_140523710, FUN_140524910, FUN_140445d30'
#: Sampling step for the hit-record walk: 60 Hz, evaluated at the middle of each step. Every
#: type-1 event in the player TAEs starts and ends on a 1/30 s boundary (counted 2026-09-29).
HIT_RECORD_STEP = 1 / 60
#: `FUN_1403f1d40` resolves an attack animation as `spAtkcategory * 1000000 + anim` when bound,
#: else `wepmotionCategory * 1000000 + anim`, else, for the right hand, `a023_<anim>` (the left
#: hand falls back to a048) (`VERIFIED`, 1.16.2).
RIGHT_HAND_FALLBACK_CATEGORY = 23

#: Crouch R1 gate, `c0000.hks` `IsUseStealthAttack` (called from `ExecAttack` lines 1406-1408 and
#: 1504-1506): when it returns `FALSE` the `W_AttackRightLightStealth` / `W_AttackBothLightStealth`
#: request is rewritten to `W_AttackRightLightStep` / `W_AttackBothLightStep`, the rolling R1. It
#: reads `env(345, hand)`, which is `EquipParamWeapon.spAtkcategory` (its literals 110, 182, 207,
#: 255, 257, 832 and 852 are exactly the spAtkcategory values of Broadsword, Morning Star,
#: Serpent-Hunter, Falx, Dancing Blade of Ranah, Starscourge Greatsword and Ornamental Straight
#: Sword), and `GetEquipType` against `WEAPON_CATEGORY_*` from `common_define.hks`, whose numbers
#: are the wepmotionCategory values (23 straight sword, 26 colossal sword, 31 colossal weapon).
#: `MEASURED` from `scripts/er-hks-disasm.py --dump IsUseStealthAttack`. Cross-check: these eight
#: categories and the spAtkcategory TAEs 129, 182 and 207 are the only ones with a 030310 entry.
STEALTH_ATTACK_CATEGORIES = frozenset({21, 23, 24, 26, 27, 28, 39, 58})
#: spAtkcategory values that return `TRUE` before the category test, in either grip.
STEALTH_ATTACK_SP_ALLOWED = frozenset({182, 207})
#: spAtkcategory values that return `FALSE` first. One-handed only 110 is refused; two-handed
#: (`c_Style` `HAND_RIGHT_BOTH`/`HAND_LEFT_BOTH`) also 255, 257, 832 and 852.
STEALTH_ATTACK_SP_REFUSED = {'one': frozenset({110}),
                             'both': frozenset({110, 255, 257, 832, 852})}
#: Powerstance crouch L1 gate, `IsUseStealthAttack(TRUE)` (called from `ExecAttack` line 1645 on
#: `ATTACK_REQUEST_DUAL_RIGHT` with `r1 == W_AttackRightLightStealth`): a different list from the
#: one-hand gate, and no spAtkcategory test. `TRUE` only when the right hand is straight sword 23,
#: twinblade 24, rapier 27, curved sword 28, spear 36, large spear 37 or backhand sword 58; every
#: other pair plays `W_AttackDualRolling`, which `c0000.behbnd` maps to state `AttackDualRolling`
#: and clip 034300 alone (`er-behbnd-attack-map.py`). `MEASURED` from `scripts/er-hks-disasm.py
#: --dump IsUseStealthAttack` (pcs 0-29). Cross-check: 034310 exists in exactly these TAEs plus
#: 39 (heavy thrusting sword), which the gate refuses, so that clip is never played.
DUAL_STEALTH_ATTACK_CATEGORIES = frozenset({23, 24, 27, 28, 36, 37, 58})

#: Per action: (label, input JumpTable ids, cancel JumpTable ids). `VERIFIED` from the
#: decompile of 0x1404275e0 and the jump table at 0x140428650 (see the module docstring).
#: Move has no input ids: `MovementRequest` only needs the stick and `taeCancels` bit 2.
RECOVERY_ACTIONS = [
    ('r1', 'R1', (1, 87), (4, 115)),
    ('r2', 'R2', (1, 87), (4, 116)),
    ('dodge', 'roll', (25, 87), (26,)),
    ('guard', 'guard', (21, 87), (22,)),
    ('move', 'move', None, (11, 78)),
]
#: `GetJuptableEarlyActivateValue` 0x14042f950 cases this tool can evaluate offline:
#: 0 falls through to 0.0, 2 is the attack hand's EquipParamWeapon.weaponWeightRate.
EARLY_DEFAULT, EARLY_WEAPON_WEIGHT_RATE = 0, 2
#: TAE times are seconds; frames here assume 30 fps (`INFERRED`, the usual FromSoft rate).
TAE_FPS = 30

#: Toughness scale (`VERIFIED`: `CSChrToughnessModule` slot 3 0x1404870d0 returns the
#: float at 0x142bb55d8, which is 100.0 in the 1.16.2 image; in 1.17.1 slot 3 is 0x140487630
#: and the float is at 0x142bb86f8).
TOUGHNESS_SCALE = 100.0

#: The `behaviorDataFactor` term of `CSChrBehaviorModule::Update` (1.16.2 0x14041d760, 1.17.1
#: 0x14041dca0): graph dt = dt * factor * debug anim speed * TAE 608 speed. The factor comes
#: from 0x140416270 (1.17.1 0x1404167a0), which returns `CSChrBehaviorDataModule+0x310` only
#: when a debug byte is set (`GlobalDebugFlags` +0x2e for other characters, +0x32 for the
#: player) and 1.0 otherwise. Both bytes are zero-initialised `.data` with no writer outside
#: the chain that registers them as named settings, so the factor is 1.0 in the
#: shipped game and frames here do not carry it (`VERIFIED` code shape, `INFERRED` that no
#: setting turns the byte on). +0x310 is written by TAE 603 `DebugAnimSpeed`.
BEHAVIOR_DATA_FACTOR = 1.0

#: EXE sites the selftest reads for the factor (1.16.2 image, 1.17.1 image).
FACTOR_SITES = {
    # movss xmm0, [rdi+0x310]: the branch taken when the debug byte is set.
    'factor_load_310': (0x145b7d662, 0x14575b2b0),
    # movss xmm0, [rip+X]: the branch taken otherwise; X holds 1.0.
    'factor_default': (0x145c9235e, 0x14528085f),
    # movzx edx, [rip+X] / movzx ecx, [rip+Y]: the two debug bytes the gate reads.
    'flag_reads': (0x140534df5, 0x140e463cd),
    # TAE 603: +0x310 = (end - start) * 30.0 / Args[0] (Args[0] > 0), else 1.0.
    'tae603_store': (0x14554a64d, 0x14204c155),
}

#: 1.17.1 addresses of the constants the formulas use (re-found through the instructions that
#: read them; see `docs/er-mechanics/attacks.md` section 9).
CONST_1171 = {
    'percent': (0x1432a18e4, 0.01),        # 1.16.2 0x14329e624
    'toughness': (0x142bb86f8, 100.0),     # 1.16.2 0x142bb55d8
    'repel_at_risk': (0x1429e8c30, -5.0),  # 1.16.2 0x1429e5c30
    'repel_broken': (0x1429d1438, -10.0),  # 1.16.2 0x1429ce438
}

#: Attack slots. Judge id and TimeAct animation are `TAE` (read from a20/a23/a26/a31);
#: the slot meaning comes from the behavior graph state that plays the animation
#: (`c0000.behbnd` hkbStateMachine state names, `scripts/er-behbnd-attack-map.py`).
#: Two-handed slots are judge + 200 and animation + 2000.
#: (key, label, judge, local animation id, behavior-graph state)
SLOTS_ONE_HAND = [
    ('r1_1', 'R1 #1', 0, 30000, 'AttackRightLight1'),
    ('r1_2', 'R1 #2', 10, 30010, 'AttackRightLight2'),
    ('r1_3', 'R1 #3', 20, 30020, 'AttackRightLight3'),
    ('r1_4', 'R1 #4', 30, 30030, 'AttackRightLight4'),
    ('r1_5', 'R1 #5', 40, 30040, 'AttackRightLight5'),
    ('r1_6', 'R1 #6', 50, 30050, 'AttackRightLight6'),
    ('r2_1', 'R2 #1', 100, 30505, 'AttackRightHeavy1End'),
    ('r2_1c', 'R2 #1 charged', 105, 30500, 'AttackRightHeavy1Start'),
    ('r2_2', 'R2 #2', 110, 30515, 'AttackRightHeavy2End'),
    ('r2_2c', 'R2 #2 charged', 115, 30510, 'AttackRightHeavy2Start'),
    ('run_r1', 'running R1', 120, 30200, 'AttackRightLightDash'),
    ('run_r2', 'running R2', 125, 30210, 'AttackRightHeavyDash'),
    ('roll_r1', 'rolling R1', 130, 30300, 'AttackRightLightStep'),
    ('bstep_r1', 'backstep R1', 140, 30400, 'AttackRightBackstep'),
    # Crouch R1: `c0000.behbnd` state `AttackRightLightStealth` plays 030310, whose TAE fires
    # the rolling R1 judge (a23 f11-14, a26 f14-16). a20/a25/a29/a31 have no such clip.
    ('crouch_r1', 'crouch R1', 130, 30310, 'AttackRightLightStealth'),
    ('jump_r1', 'jump R1', 150, 31070, 'Jump_LandAttack_Normal'),
    ('jump_r2', 'jump R2', 160, 31270, 'Jump_LandAttack_Hard'),
    ('counter', 'guard counter', 180, 30700, 'AttackRightHeavyCounter'),
]
ROLL_ANIM, CROUCH_ANIM = 30300, 30310
TWO_HAND_JUDGE_OFFSET = 200
TWO_HAND_ANIM_OFFSET = 2000

ATK_FIELDS = ['atkPhysCorrection', 'atkMagCorrection', 'atkFireCorrection',
              'atkThunCorrection', 'atkDarkCorrection', 'atkStamCorrection',
              'guardAtkRateCorrection', 'guardBreakCorrection', 'atkSuperArmorCorrection',
              'isAddBaseAtk', 'atkPhys', 'atkStam', 'guardAtkRate', 'atkSuperArmor',
              'atkAttribute', 'dmgLevel', 'isDisableBothHandsAtkBonus', 'opposeTarget',
              'friendlyTarget', 'selfTarget']
WEP_FIELDS = ['behaviorVariationId', 'wepmotionCategory', 'reinforceTypeId',
              'attackBasePhysics', 'attackBaseStamina', 'attackBaseRepel',
              'saWeaponDamage', 'staminaConsumptionRate', 'toughnessCorrectRate',
              'isValidTough_ProtSADmg', 'weaponCategory', 'wepType', 'weaponWeightRate',
              'spAtkcategory', 'atkAttribute', 'atkAttribute2']
#: SpEffect cut-rate columns by damage type, in `GetPhysicalDamageCutRateByType` 0x140d4fe90 order.
COUNTER_CUT_FIELDS = {'slash': 'slashDamageCutRate', 'strike': 'blowDamageCutRate',
                      'pierce': 'thrustDamageCutRate', 'standard': 'neutralDamageCutRate',
                      'magic': 'magicDamageCutRate', 'fire': 'fireDamageCutRate',
                      'lightning': 'thunderDamageCutRate', 'holy': 'darkDamageCutRate'}
COUNTER_BOOST_FIELDS = {'physical': 'physicsAttackRate', 'magic': 'magicAttackRate',
                        'fire': 'fireAttackRate', 'lightning': 'thunderAttackRate',
                        'holy': 'darkAttackRate'}


class Regulation:
    """The params this module reads, decoded once."""

    def __init__(self, regulation=None):
        files = PR.load(regulation)

        def table(stem, fields=None):
            rows, _, _ = PR.rows(PR.param_bytes(files, stem), fields)
            return {r['id']: r for r in rows}

        self.weapon = table('EquipParamWeapon', WEP_FIELDS)
        self.behavior = table('BehaviorParam_PC')
        self.atk = table('AtkParam_Pc', ATK_FIELDS)
        self.reinforce = table('ReinforceParamWeapon',
                               ['physicsAtkRate', 'staminaAtkRate', 'saWeaponAtkRate'])
        self.toughness = table('ToughnessParam')
        sp = table('SpEffectParam', ['stateInfo', 'effectEndurance'] + list(COUNTER_CUT_FIELDS.values())
                   + list(COUNTER_BOOST_FIELDS.values()))
        #: Counter-hit SpEffects (defender side) and counter boosts (attacker side), by id.
        self.counter_speffects = {i: {'endurance': r['effectEndurance'],
                                      **{k: r[f] for k, f in COUNTER_CUT_FIELDS.items()}}
                                  for i, r in sp.items() if r['stateInfo'] == COUNTER_STATE_INFO}
        self.counter_boosts = {i: {k: r[f] for k, f in COUNTER_BOOST_FIELDS.items()}
                               for i, r in sp.items() if r['stateInfo'] == COUNTER_BOOST_STATE_INFO}
        self.weapon_names = PR.row_names('EquipParamWeapon')
        self.atk_names = PR.row_names('AtkParam_Pc')

    def find_weapon(self, key):
        """A weapon id, or the first named row containing `key` (case-insensitive)."""
        if str(key).isdigit():
            wid = int(key)
            if wid not in self.weapon:
                raise SystemExit(f'EquipParamWeapon has no row {wid}')
            return wid
        low = str(key).lower()
        hits = sorted(i for i, n in self.weapon_names.items()
                      if n and n.lower() == low and i in self.weapon)
        hits = hits or sorted(i for i, n in self.weapon_names.items()
                              if n and low in n.lower() and not n.startswith('[')
                              and i in self.weapon)
        if not hits:
            raise SystemExit(f'no weapon named like {key!r}')
        return hits[0]

    def resolve_behavior_id(self, judge, variation):
        """`PlayerIns::ResolveBehaviorId` 0x140652280, including both fallbacks."""
        if not 0 <= judge < 10000 or variation >= 100000:  # IsValidBehaviorJudgeID 0x1406577b0
            return -1
        kind = 1 if judge < 1000 else judge // 1000
        low = judge % 1000
        row = (kind * 100000 + variation) * 1000 + low
        if row in self.behavior:
            return row
        row = (variation // 100 + kind * 1000) * 100000 + low
        if judge > 999 and row not in self.behavior:
            row = kind * 100000000 + low
        return row


def attack_numbers(reg, weapon_id, judge, level=0):
    """One attack's regulation numbers, or None when the judge resolves to nothing."""
    w = reg.weapon[weapon_id]
    bid = reg.resolve_behavior_id(judge, w['behaviorVariationId'])
    b = reg.behavior.get(bid)
    if b is None or b['refType'] != 0:
        return None
    a = reg.atk.get(b['refId'])
    if a is None:
        return None
    rf = reg.reinforce.get(w['reinforceTypeId'] + level) or {}
    sa_rate = rf.get('saWeaponAtkRate', 1.0)
    stam_rate = rf.get('staminaAtkRate', 1.0)
    add = bool(a['isAddBaseAtk'])
    poise = (w['saWeaponDamage'] * sa_rate * a['atkSuperArmorCorrection'] * 0.01
             + (a['atkSuperArmor'] if add else 0.0))
    stamina_damage = (w['attackBaseStamina'] * stam_rate * a['atkStamCorrection'] * 0.01
                      + (a['atkStam'] if add else 0))
    guard_level = (w['attackBaseRepel'] * a['guardAtkRateCorrection'] * 0.01
                   + (a['guardAtkRate'] if add else 0))
    return {
        'judge': judge,
        'behavior_row': bid,
        'atk_row': b['refId'],
        'atk_name': reg.atk_names.get(b['refId']),
        'mv_phys': a['atkPhysCorrection'],
        'mv_mag': a['atkMagCorrection'],
        'mv_fire': a['atkFireCorrection'],
        'mv_light': a['atkThunCorrection'],
        'mv_holy': a['atkDarkCorrection'],
        'poise_damage': round(poise, 4),
        'stamina_cost': int(b['stamina'] * w['staminaConsumptionRate']),
        'stamina_cost_base': b['stamina'],
        'stamina_damage': round(stamina_damage, 3),
        'guard_level_base': round(guard_level, 3),
        'guard_break_correction': a['guardBreakCorrection'],
        'dmg_level': a['dmgLevel'],
        'atk_attribute': a['atkAttribute'],
        'phys_type': resolve_phys_type(a['atkAttribute'], w),
        # The team gate reads these three flags (see `HIT_RECORD_NOTE`); a row with
        # `opposeTarget` 0 does not damage an opponent.
        'can_hit_enemy': bool(a['opposeTarget']),
    }


_TAE_CACHE = {}


def tae_animations(category):
    """{local anim id: [TaeEvent]} for `a<category>.tae`, or None when absent.

    Entries are as stored: an entry that imports another animation's events has only its own
    (often none). `resolve_events` follows the import.
    """
    if category in _TAE_CACHE:
        return _TAE_CACHE[category]
    # The binder names categories below 10 with two digits (`a00.tae`, `a02.tae`).
    path = os.path.join(PLAYER_TAE_DIR, f'a{category:02d}.tae')
    result = None
    if os.path.exists(path):
        tae = _load('er_tae_event_scan', 'er-tae-event-scan.py')
        _, result = tae.parse(path)
    _TAE_CACHE[category] = result
    return result


_REACH = None


def _reach():
    """`scripts/er-mechanics-reach.py`, loaded on first use because it loads this module.

    Its TAE mini-header reader (`tae_imports`, `hkx_source`) and its TAE 608 decoder
    (`speed_windows`) are used as they are, so both tools read the same header bytes.
    """
    global _REACH
    if _REACH is None:
        _REACH = _load('er_mechanics_reach', 'er-mechanics-reach.py')
    return _REACH


#: Import links followed before giving up (chains seen are one hop, a263 -> a137).
IMPORT_DEPTH = 4


def resolve_events(category, anim):
    """(category, anim, events) the attack's timing is read from, or (category, anim, None).

    An entry whose mini-header imports another animation (`ImportOtherAnim`, read by
    `er-mechanics-reach.tae_imports`) is followed to its source when it carries no attack
    event of its own. Its own events (sound and effects, e.g. a832_032500's type 129) are kept
    alongside the source's, as `er-mechanics-crits.resolve_anim` does (`INFERRED`: the imported
    entries hold no hitbox otherwise, e.g. Fire Knight's Greatsword a263_030000 has no events).
    An import entry that does carry type-1 events (a240, a257, a68) is read as it is, so its
    hits are not counted twice. The returned category and anim are the source's.
    """
    events = []
    for _ in range(IMPORT_DEPTH):
        own = (tae_animations(category) or {}).get(anim)
        if own is None:
            return category, anim, (events or None)
        events = events + own
        source = _reach().tae_imports(category).get(anim)
        if source is None or any(e.type == TAE_ATTACK_BEHAVIOR for e in own):
            return category, anim, events
        category, anim = source
    return category, anim, events


def clip_to_real(events):
    """Map clip seconds to real seconds under the animation's TAE 608 play-speed windows.

    TAE 608 `AnimSpeedGradient` (0x140426420, re-found in 1.17.1 at 0x140426970; decoded and
    checked in `scripts/er-mechanics-reach.py`) sets speed = start + (end - start) * progress,
    and `CSChrBehaviorModule::Update` multiplies the graph's dt by it, so a clip interval of
    length dx takes dx / speed real seconds. The integral of 1 / (s0 + k x) is exact here
    (ln for a gradient, a quotient for a constant); `er-mechanics-reach.real_seconds` sums the
    same integrand numerically and the selftest compares the two. Where windows overlap the
    later one in TAE order is taken, as in reach's `speed_at` (none of the 1235 player 608
    events overlap, and none has a speed at or below 0). The `behaviorDataFactor` term of that
    multiply is 1.0 in the shipped game (`BEHAVIOR_DATA_FACTOR`), so TAE 603 events do not
    change the result.
    """
    windows = _reach().speed_windows(events)
    if not windows:
        return lambda t: t
    cuts = sorted({x for w in windows for x in w[:2]})

    def real(t):
        total, x = 0.0, 0.0
        for b in [c for c in cuts if 0.0 < c < t] + [t]:
            if b <= x:
                continue
            mid = (x + b) / 2
            active = None
            for w in windows:
                if w[0] <= mid < w[1]:
                    active = w
            if active is None:
                total += b - x
            else:
                start, end, s0, s1 = active
                k = (s1 - s0) / (end - start)
                v0, v1 = s0 + k * (x - start), s0 + k * (b - start)
                total += (b - x) / v0 if abs(k) < 1e-9 else math.log(v1 / v0) / k
            x = b
        return total
    return real


def _tidy(frames):
    """A frame count to 0.1, as an int when whole."""
    value = round(frames, 1)
    return int(value) if value == int(value) else value


def real_frame(seconds):
    """Real 30 fps frames, to 0.1 (an int when whole)."""
    return _tidy(seconds * TAE_FPS)


def clip_frame(seconds):
    return round(seconds * TAE_FPS)


_HKX_INDEX = None
#: `hkaAnimation` fields after the type/duration pair, as laid out in the 2018 tagfile `DATA`
#: section: numFrames, numBlocks, maxFramesPerBlock, maskAndQuantizationSize, then
#: blockDuration, blockInverseDuration, frameDuration (`hkaSplineCompressedAnimation`).
HKX_SPLINE_FIELDS_OFFSET = 0x28
#: `hkaAnimationType` values; 3 is spline-compressed, the only kind seen in these binders.
HKX_ANIMATION_TYPES = (1, 2, 3, 4, 5)


def hkx_path(category, anim):
    """The unpacked `a<cat>_<anim>.hkx`, or None. Indexes every `c0000_*` shard once."""
    global _HKX_INDEX
    if _HKX_INDEX is None:
        _HKX_INDEX = {}
        if os.path.isdir(PLAYER_HKX_ROOT):
            for shard in sorted(os.listdir(PLAYER_HKX_ROOT)):
                if not shard.startswith('c0000_'):
                    continue
                for dirpath, _, files in os.walk(os.path.join(PLAYER_HKX_ROOT, shard)):
                    for name in files:
                        if name.endswith('.hkx'):
                            _HKX_INDEX.setdefault(name, os.path.join(dirpath, name))
    return _HKX_INDEX.get(f'a{category:03d}_{anim:06d}.hkx')


def hkx_duration(path):
    """(duration seconds, numFrames, frameDuration) of a spline-compressed clip, or None.

    The object is found by its own consistency: `(numFrames - 1) * frameDuration ==
    duration` and `blockDuration * blockInverseDuration == 1`. A file with no such object
    (or more than one) returns None rather than a guess.
    """
    with open(path, 'rb') as handle:
        b = handle.read()
    found = []
    for off in range(0, len(b) - HKX_SPLINE_FIELDS_OFFSET - 28, 4):
        kind, duration = struct.unpack_from('<If', b, off)
        if kind not in HKX_ANIMATION_TYPES or not 0 < duration < 600:
            continue
        frames, _, _, _, block, block_inv, frame_dt = struct.unpack_from(
            '<IIIIfff', b, off + HKX_SPLINE_FIELDS_OFFSET)
        if (0 < frames < 100000 and 0 < frame_dt < 1 and abs(block * block_inv - 1) < 1e-3
                and abs((frames - 1) * frame_dt - duration) < 1e-3):
            found.append((duration, frames, frame_dt))
    return found[0] if len(found) == 1 else None


def _early_interval(event, threshold):
    """Seconds during which a type-300 event opens its id, or None (0x140425ba0).

    Weight runs from WeightAtEventStart to WeightAtEventEnd over the event; the id is set
    while `threshold < weight`.
    """
    _, _, w_start, w_end = struct.unpack_from('<hhff', event.params, 0)
    span = event.end - event.start
    if span <= 0:
        return None
    if w_end == w_start:
        return (event.start, event.end) if threshold < w_start else None
    p = (threshold - w_start) / (w_end - w_start)
    if w_end > w_start:
        return None if p >= 1 else (event.start + max(p, 0.0) * span, event.end)
    return None if p <= 0 else (event.start, event.start + min(p, 1.0) * span)


def recovery_windows(events, weapon_weight_rate):
    """{action: {'input': [(s, e)], 'cancel': [(s, e)], 'unresolved_early': n}} in seconds."""
    out = {key: {'input': [], 'cancel': [], 'unresolved_early': 0}
           for key, _, _, _ in RECOVERY_ACTIONS}
    for e in events:
        if e.type == TAE_JUMP_TABLE:
            jid = struct.unpack_from('<i', e.params, 0)[0]
            if struct.unpack_from('<H', e.params, JUMP_TABLE_STATE_GATE_OFFSET)[0]:
                continue  # SpEffect-gated; not part of the base timing
            for key, _, inputs, cancels in RECOVERY_ACTIONS:
                if inputs and jid in inputs:
                    out[key]['input'].append((e.start, e.end))
                if jid in cancels:
                    out[key]['cancel'].append((e.start, e.end))
        elif e.type == TAE_JUMP_TABLE_EARLY:
            jid, early_type = struct.unpack_from('<hh', e.params, 0)
            for key, _, _, cancels in RECOVERY_ACTIONS:
                if jid not in cancels:
                    continue
                if early_type == EARLY_DEFAULT:
                    window = _early_interval(e, 0.0)
                elif early_type == EARLY_WEAPON_WEIGHT_RATE:
                    window = _early_interval(e, weapon_weight_rate)
                else:
                    out[key]['unresolved_early'] += 1
                    continue
                if window:
                    out[key]['cancel'].append(window)
    return out


def _first_open(windows, gated):
    """Earliest time an action can start: an input and a cancel window overlap there.

    An ungated action (move) needs only its cancel window.
    """
    if not gated:
        starts = [c[0] for c in windows['cancel']]
    else:
        starts = [max(i[0], c[0]) for i in windows['input'] for c in windows['cancel']
                  if max(i[0], c[0]) < min(i[1], c[1])]
    return min(starts) if starts else None


def _bound(category, anim):
    anims = tae_animations(category)
    return anims is not None and anim in anims


def motion_category(w, anim, right_hand_fallback=True):
    """The TAE file an attack animation is read from, in `FUN_1403f1d40`'s order.

    A weapon with a unique moveset carries `EquipParamWeapon.spAtkcategory` (Erdsteel Dagger 103,
    Dagger 0), and `a<spAtkcategory>.tae` holds only the animations that moveset replaces:
    `a103.tae` has the R2s 030500..032515 and nothing else. Then `a<wepmotionCategory>.tae`, then
    (right hand) `a023.tae` (see `RIGHT_HAND_FALLBACK_CATEGORY`). "Bound" is read here as "the
    TAE has an entry with that id", import entries included (`INFERRED`: the EXE's lookup table
    was not compared against the TAE entry lists). When nothing has it the weapon's own category
    is returned and the caller finds no entry there."""
    sp = w.get('spAtkcategory') or 0
    if sp and _bound(sp, anim):
        return sp
    category = w['wepmotionCategory']
    if (right_hand_fallback and not _bound(category, anim)
            and _bound(RIGHT_HAND_FALLBACK_CATEGORY, anim)):
        return RIGHT_HAND_FALLBACK_CATEGORY
    return category


def uses_stealth_attack(w, grip='one'):
    """`IsUseStealthAttack(FALSE)` for the right hand: does a crouch R1 play the crouch clip.

    See `STEALTH_ATTACK_CATEGORIES`. `False` means the behavior script plays the rolling R1."""
    sp = w.get('spAtkcategory') or 0
    if sp in STEALTH_ATTACK_SP_REFUSED[grip]:
        return False
    if sp in STEALTH_ATTACK_SP_ALLOWED:
        return True
    return w['wepmotionCategory'] in STEALTH_ATTACK_CATEGORIES


def uses_dual_stealth_attack(w):
    """`IsUseStealthAttack(TRUE)`: does a powerstanced crouch L1 play 034310 (`True`) or the
    rolling L1 034300 (`False`). See `DUAL_STEALTH_ATTACK_CATEGORIES`."""
    return w['wepmotionCategory'] in DUAL_STEALTH_ATTACK_CATEGORIES


def slot_animation(w, key, anim, grip='one'):
    """(anim, TAE category, note) a slot plays: the crouch R1 of a weapon `IsUseStealthAttack`
    refuses is the rolling R1 (note 'rolling R1'), every other slot is its own animation.

    The a023 fallback of `FUN_1403f1d40` is not taken. Whether a slot's behavior state is entered
    at all is the behavior script's call, and for the slots it would fill it gives rows the game
    does not show: bows an a023 R1 chain, staves and seals an a023 jump R1."""
    note = None
    if key.endswith('crouch_r1') and not uses_stealth_attack(w, grip):
        anim, note = anim - (CROUCH_ANIM - ROLL_ANIM), 'rolling R1'
    return anim, motion_category(w, anim, right_hand_fallback=False), note


def clip_length(category, anim):
    """(duration s, numFrames, frameDuration) of the HKX clip a TAE entry plays, or None.

    An entry can play another entry's clip (mini-header `ImportsHKX`, read by
    `er-mechanics-reach.hkx_source`): the crouch R1 a026_030310 plays a026_030300's."""
    clip_cat, clip_anim = _reach().hkx_source(category, anim)
    path = hkx_path(clip_cat, clip_anim) or hkx_path(category, anim)
    return hkx_duration(path) if path else None


def recovery_details(w, anim, events, hit_windows, category=None, to_real=None):
    """Per-action first frame (30 fps, from animation start) and animation length.

    `hit_windows` are frames in the same time base as the result. `to_real` maps clip seconds to
    real seconds (`clip_to_real`); without it every frame is clip time, which is what callers
    that pass their own clip-time hit frames get (`er-mechanics-powerstance-guard.py`).
    """
    to_frame = clip_frame if to_real is None else (lambda s: real_frame(to_real(s)))
    windows = recovery_windows(events, w['weaponWeightRate'])
    hit_end = max((h[1] for h in hit_windows), default=None)
    cancel, after_hit, input_open, unresolved = {}, {}, {}, {}
    for key, _, inputs, _ in RECOVERY_ACTIONS:
        win = windows[key]
        t = _first_open(win, inputs is not None)
        cancel[key] = to_frame(t) if t is not None else None
        after_hit[key] = (_tidy(cancel[key] - hit_end)
                          if cancel[key] is not None and hit_end is not None else None)
        if inputs is not None:
            input_open[key] = (to_frame(min(i[0] for i in win['input']))
                               if win['input'] else None)
        if win['unresolved_early']:
            unresolved[key] = win['unresolved_early']
    if category is None:
        category = motion_category(w, anim)
    clip = clip_length(category, anim)
    return {
        'cancel_frame': cancel,
        'recovery_after_hit': after_hit,
        'input_open_frame': input_open,
        'unresolved_early_events': unresolved,
        'anim_frames': to_frame(clip[0]) if clip else None,
        'anim_frame_duration': round(clip[2], 6) if clip else None,
    }


def hit_records(claims):
    """Hit records opened by each type-1 event, following `HIT_RECORD_NOTE`.

    `claims` is [(start s, end s, judge, attack index)] in TAE order, for events the game hands
    to `FUN_1404428f0` (not gated, judge resolved). Returns, per event, (records it opens, the
    different judge whose record on the same index it directly replaced or None). An event is
    active while start <= t < end; in a step where several events want one index the first in
    TAE order holds it (`INFERRED`: the events are taken to run in stored order).
    """
    opened = [0] * len(claims)
    follows = [None] * len(claims)
    if not claims:
        return list(zip(opened, follows))
    first = min(c[0] for c in claims)
    last = max(c[1] for c in claims)
    step = HIT_RECORD_STEP
    held = {}  # index -> judge held in the previous step
    k = math.floor(first / step)
    while (k + 0.5) * step < last:
        t = (k + 0.5) * step
        now = {}
        for pos, (start, end, judge, index) in enumerate(claims):
            if start <= t < end and index not in now:
                now[index] = (judge, pos)
        for index, (judge, pos) in now.items():
            before = held.get(index)
            if before != judge:
                opened[pos] += 1
                if before is not None and follows[pos] is None:
                    follows[pos] = before
        held = {i: j for i, (j, _) in now.items()}
        k += 1
    return list(zip(opened, follows))


def tae_details(reg, weapon_id, anim, judge, category=None):
    """Hit windows, hit count and hyperarmor windows of one animation.

    Every frame value is real time (30 fps, TAE 608 play speed applied, see `clip_to_real`);
    the clip-time value sits beside it under a `_clip` name (`hit_windows_clip`,
    `cancel_frame_clip`, `frames_clip` inside hyperarmor / counter / extra-hitbox entries).
    """
    w = reg.weapon[weapon_id]
    if category is None:
        category = motion_category(w, anim)
    src_cat, src_anim, events = resolve_events(category, anim)
    if events is None:
        return None
    to_real = clip_to_real(events)

    def frames(e):
        return (real_frame(to_real(e.start)), real_frame(to_real(e.end)))

    def frames_clip(e):
        return (clip_frame(e.start), clip_frame(e.end))

    variation = w['behaviorVariationId']
    claimed = []
    for e in events:
        if e.type != TAE_ATTACK_BEHAVIOR:
            continue
        if struct.unpack_from('<H', e.params, ATTACK_STATE_GATE_OFFSET)[0]:
            continue  # SpEffect-gated hitbox (see ATTACK_STATE_GATE_OFFSET)
        j, index = struct.unpack_from('<i', e.params, 8)[0], \
            struct.unpack_from('<i', e.params, ATTACK_INDEX_OFFSET)[0]
        if reg.resolve_behavior_id(j, variation) < 0:
            continue  # AttackBehavior never reaches FUN_1404428f0 for it
        claimed.append((e, j, index))
    records = hit_records([(e.start, e.end, j, index) for e, j, index in claimed])
    own = attack_numbers(reg, weapon_id, judge)
    own_enemy = bool(own and own['can_hit_enemy'])
    windows, windows_clip, window_detail, other = [], [], [], []
    for (e, j, index), (opened, follows) in zip(claimed, records):
        if j == judge:
            windows.append(frames(e))
            windows_clip.append(frames_clip(e))
            hits = opened if own_enemy else 0
            window_detail.append({'frames': frames(e), 'attack_index': index,
                                  'hit_records': opened, 'follows_judge': follows,
                                  'hits': hits, 'sweep_hit': bool(hits) and follows is None})
            continue
        extra = attack_numbers(reg, weapon_id, j)
        if extra and extra['mv_phys'] + extra['mv_mag'] + extra['mv_fire'] \
                + extra['mv_light'] + extra['mv_holy'] > 0:
            other.append({'judge': j, 'frames': frames(e), 'frames_clip': frames_clip(e),
                          'mv_phys': extra['mv_phys'], 'poise_damage': extra['poise_damage'],
                          'attack_index': index, 'hit_records': opened, 'follows_judge': follows,
                          'can_hit_enemy': extra['can_hit_enemy'],
                          'hits': opened if extra['can_hit_enemy'] else 0,
                          'sweep_hit': extra['can_hit_enemy'] and bool(opened) and follows is None})
    own_hits = sum(d['hits'] for d in window_detail)
    own_sweep = sum(d['sweep_hit'] for d in window_detail)
    hyper = []
    for e in events:
        if e.type != TAE_TOUGHNESS:
            continue
        row_id, source = e.params[0], e.params[1]
        ratio = struct.unpack_from('<f', e.params, 4)[0]
        t = reg.toughness.get(row_id, {})
        weapon_term = source in (1, 2)
        bonus = (TOUGHNESS_SCALE * t.get('correctionRate', 0.0) * w['toughnessCorrectRate']
                 if weapon_term else 0.0)
        hyper.append({
            'frames': frames(e),
            'frames_clip': frames_clip(e),
            'toughness_row': row_id,
            'poise_bonus': round(bonus, 3),
            'armor_scale': t.get('proCorrectionRate'),
            'floor_pct_of_max': t.get('minToughness'),
            'poise_damage_taken_ratio': round(ratio, 3),
            'pvp_poise_damage_taken': t.get('unk1'),
            'pvp_hp_damage_taken': t.get('unk2'),
        })
    # Counter frames: the animation puts a stateInfo-110 SpEffect on the attacker, so a hit
    # taken inside the window is multiplied by that SpEffect's cut rate for the incoming
    # damage type. The effect is refreshed while the event runs and lapses `endurance`
    # seconds after it ends (SpEffect 45: 0.1 s), which is not added to the frames here.
    counter = [{'frames': frames(e), 'frames_clip': frames_clip(e),
                'speffect': struct.unpack_from('<i', e.params, 0)[0]}
               for e in events if e.type == TAE_ADD_SPEFFECT and len(e.params) >= 4
               and struct.unpack_from('<i', e.params, 0)[0] in reg.counter_speffects]
    real = recovery_details(w, src_anim, events, windows, src_cat, to_real)
    clip = recovery_details(w, src_anim, events, windows_clip, src_cat)
    speed = [(clip_frame(s), clip_frame(e), round(a, 3), round(b, 3))
             for s, e, a, b in _reach().speed_windows(events)]
    return {'source': (src_cat, src_anim), 'imported': (src_cat, src_anim) != (category, anim),
            'hit_windows': windows, 'hit_windows_clip': windows_clip, 'hits': len(windows),
            'hit_window_detail': window_detail, 'own_hits': own_hits,
            'max_hits': own_hits + sum(o['hits'] for o in other),
            'own_sweep_hits': own_sweep,
            'sweep_hits': own_sweep + sum(o['sweep_hit'] for o in other),
            'other_hitboxes': other, 'hyperarmor': hyper, 'counter_windows': counter,
            'speed_windows': speed, **real,
            **{f'{k}_clip': clip[k] for k in ('cancel_frame', 'recovery_after_hit',
                                              'input_open_frame', 'anim_frames')}}


def weapon_attacks(reg, weapon_id, grip='one', level=0):
    w = reg.weapon[weapon_id]
    out = []
    for key, label, judge, anim, state in SLOTS_ONE_HAND:
        if grip == 'both':
            key, label = '2h_' + key, '2H ' + label
            judge, anim = judge + TWO_HAND_JUDGE_OFFSET, anim + TWO_HAND_ANIM_OFFSET
            state = state.replace('AttackRight', 'AttackBoth')
        nums = attack_numbers(reg, weapon_id, judge, level)
        if nums is None:
            continue
        # See `slot_animation`: a colossal weapon's crouch R1 is its rolling R1, never the
        # a023 straight-sword clip the EXE fallback would find.
        anim, category, crouch_note = slot_animation(w, key, anim, grip)
        tae = tae_details(reg, weapon_id, anim, judge, category)
        if tae is not None and crouch_note:
            tae['crouch_fallback'] = crouch_note
        if tae is None and crouch_note:
            continue
        if tae is not None and not tae['hits'] and not tae['other_hitboxes']:
            continue  # the category has no such animation hit (e.g. a 4-hit R1 chain)
        src_cat, src_anim = tae['source'] if tae else (category, anim)
        row = {'slot': key, 'label': label, 'anim': f"a{src_cat:03d}_{src_anim:06d}",
               'tae_entry': f"a{category:03d}_{anim:06d}", 'state': state, **nums}
        if tae is None:
            # Attack numbers with no attack event in the animation: a bow's shot, or a staff's or
            # seal's jumping attack. The row keeps its numbers and says it has no hit window.
            row.update(hit_windows=None, hits=None, other_hitboxes=[])
        else:
            row.update(tae)
            if state.endswith(('Heavy1End', 'Heavy2End')):
                lead = release_lead_in(w, anim - R2_RELEASE_ANIM_OFFSET)
                if lead is not None:
                    row['release_lead_in'], row['release_lead_in_clip'] = lead
        out.append(row)
    return out


#: An uncharged R2 is two clips: `Heavy<n>Start` (030500, 030510) runs until the button is up and
#: the release is allowed, then `Heavy<n>End` (030505, 030515) plays the swing. Its frames above
#: are measured from the start of the End clip.
R2_RELEASE_ANIM_OFFSET = 5
#: SpEffect the Start clip applies (TAE 66) from the first frame a release may end the charge:
#: `AttackRightHeavy1Start_onUpdate` moves to `W_AttackRightHeavy1End` when R2 is up and
#: `GetGeneralTAEFlag(TAE_FLAG_CHARGING) == 1 or GetSpEffectID(100280)` (Smithbox `c0000.hks`
#: line 7768, `COMMUNITY` decompile). The flag is bit n of `CSChrBehaviorDataModule+0x308`, cleared
#: every frame and set only by TAE event 600, and no player R2 Start/End clip carries an event 600
#: (1164 clips checked), so the SpEffect's first frame is the earliest release (`VERIFIED`, 1.16.2
#: image; docs/er-mechanics/frame-advantage.md). Whether the script sees the SpEffect on the frame
#: it is applied or the next is not traced.
R2_RELEASE_SPEFFECT = 100280
#: The Start clips carry that SpEffect on TAE event 67 (Golem's Halberd a198_032500 f17-29);
#: events 66 and 67 go through the same `AddSpEffect` handler, so both are accepted.
R2_RELEASE_EVENT_TYPES = (TAE_ADD_SPEFFECT, 67)


def release_lead_in(w, start_anim):
    """(real, clip) frames from the Start clip's first frame to its first `R2_RELEASE_SPEFFECT`
    event, or None when the clip or the event is absent."""
    category = motion_category(w, start_anim, right_hand_fallback=False)
    _, _, events = resolve_events(category, start_anim)
    if events is None:
        return None
    starts = [e.start for e in events if e.type in R2_RELEASE_EVENT_TYPES and len(e.params) >= 4
              and struct.unpack_from('<i', e.params, 0)[0] == R2_RELEASE_SPEFFECT]
    if not starts:
        return None
    t = min(starts)
    return real_frame(clip_to_real(events)(t)), clip_frame(t)


def print_table(reg, weapon_id, rows):
    w = reg.weapon[weapon_id]
    print(f"{reg.weapon_names.get(weapon_id)} ({weapon_id}) variation {w['behaviorVariationId']}"
          f" motion {w['wepmotionCategory']} saWeaponDamage {w['saWeaponDamage']}"
          f" staminaConsumptionRate {round(w['staminaConsumptionRate'], 4)}"
          f" toughnessCorrectRate {round(w['toughnessCorrectRate'], 4)}")
    recovery_cols = ''.join(f' {label:>5}' for _, label, _, _ in RECOVERY_ACTIONS)
    print(f"{'slot':16} {'judge':>5} {'atk row':>9} {'type':>8} {'MV':>4} {'poise':>7} {'stam':>4} "
          f"{'stDmg':>6} {'hits':>4}{recovery_cols} {'len':>4}  windows  hyperarmor  counter")
    for r in rows:
        hyper = '; '.join(f"f{h['frames'][0]}-{h['frames'][1]} +{h['poise_bonus']}"
                          f" (row {h['toughness_row']})" for h in r.get('hyperarmor', []))
        extra = ''.join(f" +j{o['judge']}:{o['mv_phys']}(i{o['attack_index']} x{o['hits']})"
                        for o in r.get('other_hitboxes', []))
        cancel = r.get('cancel_frame', {})
        opens = ''.join(f" {_dash(cancel.get(key)):>5}" for key, _, _, _ in RECOVERY_ACTIONS)
        counter = '; '.join(f"f{c['frames'][0]}-{c['frames'][1]} sp{c['speffect']}"
                            for c in r.get('counter_windows', []))
        print(f"{r['label']:16} {r['judge']:>5} {r['atk_row']:>9} {r['phys_type']:>8} {r['mv_phys']:>4} "
              f"{r['poise_damage']:>7.2f} {r['stamina_cost']:>4} {r['stamina_damage']:>6.1f} "
              f"{str(r.get('sweep_hits', '-')) + '/' + str(r.get('max_hits', '-')):>4}{opens} {_dash(r.get('anim_frames')):>4}"
              f"  {r.get('hit_windows') or ''}{extra}  {hyper}  {counter}")
    print('Frames are real time: 30 fps from animation start with the TAE 608 play speed'
          ' applied (clip-time values are the *_clip fields in --json).'
          ' hits = sweep/max: max is the most times one target can be hit (one per hit record),'
          ' sweep leaves out segments that take an index over from another judge;'
          ' +jJ:MV(iI xN) is an extra hitbox on attack index I adding N to max.'
          ' R1/R2/roll/guard/move = first frame that action can start; len = clip length.'
          ' Recovery after the hit = that frame - last hit frame.'
          ' counter = frames this attack carries a counter-hit SpEffect (hits taken there are'
          ' multiplied by its cut rate for their damage type).')


def _dash(value):
    return '-' if value is None else value


def _read_f32(path, va):
    with open(path, 'rb') as handle:
        handle.seek(va - 0x140000000)
        return struct.unpack('<f', handle.read(4))[0]


#: 1.16.2 helpers the JumpTable cases call (names from the named Ghidra dump on :8765).
SET_ALLOWED_CANCEL = 0x140407af0     # CSChrActionRequestModule::SetAllowedCancelToActionState
CANCEL_MOVEMENT = 0x140407c00        # CSChrActionRequestModule::CancelMovement
ALLOW_INPUT_RH_ATTACK = 0x1404300e0  # CSChrTaeAnimEvent::AllowInputRHAttack
ALLOW_INPUT_DODGE = 0x140430150      # CSChrTaeAnimEvent::AllowInputDodge
#: ChrActionType values passed in EDX (same dump: `R1` 0, `R2` 1, `ROLLING` 0x11).
ACT_R1, ACT_R2, ACT_ROLLING = 0, 1, 0x11
#: `_ChrActionFlag` dispatch: `dec eax; cmp eax, 0x8e; jmp [0x140428650 + eax*4]`.
JT_TABLE, JT_COUNT = 0x140428650, 0x8f
#: `ActivateChrActionFlagEarly` dispatch: `add eax, -4; cmp eax, 0x78`, byte index table
#: then dword table; 0x140426333 is the shared no-op exit.
EARLY_INDEX, EARLY_TABLE, EARLY_FIRST_ID, EARLY_COUNT = 0x14042639c, 0x140426348, 4, 0x79
EARLY_NOOP = 0x140426333
#: `GetJuptableEarlyActivateValue` dispatch: `dec eax; cmp eax, 5; jmp [0x14042f9f0 + eax*4]`.
EARLY_VALUE_TABLE = 0x14042f9f0
IMAGE_BASE = 0x140000000
#: A case body is read up to the next case entry, and never further than this.
CASE_SPAN_CAP = 0x100


def _image_read(path, va, size):
    with open(path, 'rb') as handle:
        handle.seek(va - IMAGE_BASE)
        return handle.read(size)


def _rip_target(path, va, length):
    """Target of the rip-relative operand of an instruction whose disp32 is its last 4 bytes."""
    return va + length + struct.unpack('<i', _image_read(path, va + length - 4, 4))[0]


def _in_bss(path, va):
    """True when `va` lies in a section's zero-filled tail (past its raw data)."""
    head = _image_read(path, IMAGE_BASE, 0x1000)
    pe = struct.unpack_from('<I', head, 0x3c)[0]
    count, opt = struct.unpack_from('<H', head, pe + 6)[0], struct.unpack_from('<H', head, pe + 20)[0]
    rva = va - IMAGE_BASE
    for i in range(count):
        vsize, vaddr, rsize = struct.unpack_from('<III', head, pe + 24 + opt + 40 * i + 8)
        if vaddr <= rva < vaddr + vsize:
            return rva >= vaddr + rsize
    return False


def _factor_checks(check, skips):
    """The `behaviorDataFactor` gate and TAE 603, byte for byte in both images."""
    for idx, (build, path) in enumerate((('1.16.2', DEOBF_1162), ('1.17.1', DEOBF_1171))):
        if not os.path.exists(path):
            skips.append(f'behaviorDataFactor {build}: {path} absent')
            continue
        src = f'EXE {os.path.basename(path)}'
        site = FACTOR_SITES['factor_load_310'][idx]
        check(f'{build} factor debug branch is movss xmm0, [rdi+0x310]',
              _image_read(path, site, 8).hex(), 'f30f108710030000', src)
        site = FACTOR_SITES['factor_default'][idx]
        check(f'{build} factor default branch loads 1.0',
              (_image_read(path, site, 4).hex(), _read_f32(path, _rip_target(path, site, 8))),
              ('f30f1005', 1.0), src)
        site = FACTOR_SITES['flag_reads'][idx]
        code = _image_read(path, site, 19)
        flag_all, flag_player = _rip_target(path, site, 7), _rip_target(path, site + 9, 7)
        check(f'{build} gate reads two debug bytes 4 apart, player one under cmovne',
              (code[:3].hex(), code[7:12].hex(), code[16:19].hex(), flag_player - flag_all),
              ('0fb615', '84c00fb60d', '0f45d1', 4), src)
        check(f'{build} gate bytes are zero-filled .data',
              (_in_bss(path, flag_all), _in_bss(path, flag_player)), (True, True), src)
        site = FACTOR_SITES['tae603_store'][idx]
        code = _image_read(path, site, 41)
        check(f'{build} TAE 603 stores (end - start) * 30 / Args[0] at +0x310',
              (code[:24].hex(), code[28:].hex(), _read_f32(path, _rip_target(path, site + 20, 8))),
              ('488b4220660f6ec10f5bc0f30f104808f30f5c08f30f590d',
               'f30f5ec8f3410f118810030000', 30.0), src)


def _jump_table_case_calls(path):
    """{JumpTable id: {(helper VA, EDX or None)}} for every `_ChrActionFlag` case."""
    table = struct.unpack(f'<{JT_COUNT}I', _image_read(path, JT_TABLE, 4 * JT_COUNT))
    targets = [IMAGE_BASE + t for t in table]
    starts = sorted(set(targets))
    helpers = (SET_ALLOWED_CANCEL, CANCEL_MOVEMENT, ALLOW_INPUT_RH_ATTACK, ALLOW_INPUT_DODGE)
    out = {}
    for index, start in enumerate(targets):
        later = [s for s in starts if s > start]
        end = min(later[0] if later else start + CASE_SPAN_CAP, start + CASE_SPAN_CAP)
        body = _image_read(path, start, end - start)
        found = set()
        for off in range(len(body) - 4):
            if body[off] != 0xe8:
                continue
            callee = start + off + 5 + struct.unpack_from('<i', body, off + 1)[0]
            if callee not in helpers:
                continue
            edx = None
            if callee == SET_ALLOWED_CANCEL:
                # The nearest `mov edx, imm32` (ba) or `xor edx, edx` (33 d2) before the call.
                back = body[max(0, off - 12):off]
                mov_at, xor_at = back.rfind(b'\xba'), back.rfind(b'\x33\xd2')
                if mov_at >= 0 and mov_at + 5 <= len(back) and mov_at > xor_at:
                    edx = struct.unpack_from('<I', back, mov_at + 1)[0]
                elif xor_at >= 0:
                    edx = 0
            found.add((callee, edx))
        out[index + 1] = found
    return out


def _early_jump_table_ids(path):
    """JumpTable ids that `ActivateChrActionFlagEarly` handles (not the no-op exit)."""
    index = _image_read(path, EARLY_INDEX, EARLY_COUNT)
    table_len = max(index) + 1
    table = struct.unpack(f'<{table_len}I', _image_read(path, EARLY_TABLE, 4 * table_len))
    return {EARLY_FIRST_ID + i for i, slot in enumerate(index)
            if IMAGE_BASE + table[slot] != EARLY_NOOP}


def _early_value_case(path, early_type):
    """The indirect jump of one `GetJuptableEarlyActivateValue` case (after two movs)."""
    target = IMAGE_BASE + struct.unpack(
        '<I', _image_read(path, EARLY_VALUE_TABLE + 4 * (early_type - 1), 4))[0]
    return _image_read(path, target + 6, 7)


def _template_jump_table_names(path):
    """{id: entry name} from the `FlagType` list of event 0 in a WitchyBND TAE template."""
    import re
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    start = text.find('<event id="0"')
    block = text[start:text.find('</event>', start)] if start >= 0 else ''
    return {int(v): n for n, v in re.findall(r'<entry name="([^"]*)" value="(-?\d+)"', block)}


def selftest():
    """Check values against references that do not come from this module's own tables."""
    reg = Regulation()
    failures, passes, skips = [], [], []

    def check(name, got, want, source):
        (passes if got == want else failures).append(f'{name}: got {got!r} want {want!r} [{source}]')

    # 1. ResolveBehaviorId, formula read from the decompile of 0x140652280.
    exe = 'EXE 0x140652280'
    check('dagger R1 behavior row', reg.resolve_behavior_id(0, 100), 100100000, exe)
    check('greatsword R1 falls back to family 400',
          reg.resolve_behavior_id(0, 401), 100400000, exe)
    check('ash-of-war judge 3310 on a dagger', reg.resolve_behavior_id(3310, 100), 300100310, exe)

    # 2. Smithbox community row names are authored independently of the slot table here.
    dagger = 1000000
    names = {r['slot']: r['atk_name'] for r in weapon_attacks(reg, dagger)}
    names2 = {r['slot']: r['atk_name'] for r in weapon_attacks(reg, dagger, 'both')}
    src = 'COMMUNITY: Smithbox Param Row Names, AtkParam_Pc'
    check('dagger R1 #6 row name', names.get('r1_6'), 'Default - Dagger - 1H Light #6', src)
    check('dagger charged R2 row name', names.get('r2_1c'),
          'Default - Dagger - 1H Heavy #1 Max', src)
    check('dagger 2H R1 #1 row name', names2.get('2h_r1_1'),
          'Default - Dagger - 2H Light #1', src)

    # 3. TimeAct: the judge each dagger animation fires is read straight from a20.tae.
    anims = tae_animations(20)
    if anims is None:
        skips.append('TAE judge checks: ' + PLAYER_TAE_DIR + ' absent')
    else:
        for key, _, judge, anim, _ in SLOTS_ONE_HAND[:10]:
            fired = [struct.unpack_from('<i', e.params, 8)[0] for e in anims.get(anim, [])
                     if e.type == TAE_ATTACK_BEHAVIOR]
            check(f'a020_{anim:06d} fires judge', judge in fired, True, 'TAE a20.tae')

    # 4. The "51 poise" PvE breakpoint (COMMUNITY: survives one straight-sword or katana
    #    R1). In menu units that is poise damage 50 per hit, i.e. 5.0 internal. Between players
    #    FinalDamageRateParam.saRate multiplies it (2.2 on these rows, 110 menu).
    src = 'COMMUNITY: 51-poise PvE breakpoint (Fextralife Poise page)'
    for name, wid in (('Longsword', 2000000), ('Uchigatana', 9000000)):
        r1 = next(r for r in weapon_attacks(reg, wid) if r['slot'] == 'r1_1')
        check(f'{name} R1 poise x10 below 51', round(r1['poise_damage'] * 10) < 51
              and round(r1['poise_damage'] * 10) >= 50, True, src)

    # 5. Hyperarmor presence (COMMUNITY: straight-sword R1s have none, colossal R1s do).
    if tae_animations(23) is None or tae_animations(26) is None:
        skips.append('hyperarmor presence: TAE absent')
    else:
        src = 'COMMUNITY: colossal weapons have R1 hyperarmor, straight swords do not'
        ls = next(r for r in weapon_attacks(reg, 2000000) if r['slot'] == 'r1_1')
        gs = next(r for r in weapon_attacks(reg, 4000000) if r['slot'] == 'r1_1')
        check('Longsword R1 hyperarmor windows', len(ls['hyperarmor']), 0, src)
        check('Greatsword R1 hyperarmor windows', len(gs['hyperarmor']) > 0, True, src)

    # 6. The constants the formulas use, read out of the 1.16.2 image.
    if os.path.exists(DEOBF_1162):
        check('0.01 at 0x14329e624', round(_read_f32(DEOBF_1162, 0x14329e624), 6), 0.01,
              'EXE eldenring-deobf.bin')
        check('toughness scale at 0x142bb55d8', _read_f32(DEOBF_1162, 0x142bb55d8),
              TOUGHNESS_SCALE, 'EXE eldenring-deobf.bin')
    else:
        skips.append('EXE constants: ' + DEOBF_1162 + ' absent')
    # 6b. The same constants in 1.17.1, each read through an instruction of the re-found
    #     function (rather than trusting the table): stamina damage's mulss, toughness slot 3,
    #     and the repel function's durability terms.
    if os.path.exists(DEOBF_1171):
        src = 'EXE eldenring-deobf-1.17.1.bin'
        for name, (va, value) in CONST_1171.items():
            check(f'1.17.1 {name} at {va:#x}', round(_read_f32(DEOBF_1171, va), 6), value, src)
        check('1.17.1 stamina damage mulss 0x14068ba51 reads the 0.01',
              _rip_target(DEOBF_1171, 0x14068ba51, 8), CONST_1171['percent'][0], src)
        check('1.17.1 toughness slot 3 0x140487630 returns the 100.0',
              _rip_target(DEOBF_1171, 0x140487630, 8), CONST_1171['toughness'][0], src)
        check('1.17.1 repel 0x14068d0d7 / 0x14068d0e1 read -10.0 / -5.0',
              (_rip_target(DEOBF_1171, 0x14068d0d7, 8), _rip_target(DEOBF_1171, 0x14068d0e1, 8)),
              (CONST_1171['repel_broken'][0], CONST_1171['repel_at_risk'][0]), src)
    else:
        skips.append('1.17.1 constants: ' + DEOBF_1171 + ' absent')
    _factor_checks(check, skips)

    # 7. Recovery ids, read out of the 1.16.2 image rather than from RECOVERY_ACTIONS'
    #    own reasoning: which helper each JumpTable case calls, and with which action.
    if os.path.exists(DEOBF_1162):
        src = 'EXE jump table 0x140428650 in eldenring-deobf.bin'
        calls = _jump_table_case_calls(DEOBF_1162)
        want = {
            4: {(SET_ALLOWED_CANCEL, ACT_R1), (SET_ALLOWED_CANCEL, ACT_R2)},
            115: {(SET_ALLOWED_CANCEL, ACT_R1)},
            116: {(SET_ALLOWED_CANCEL, ACT_R2)},
            26: {(SET_ALLOWED_CANCEL, ACT_ROLLING)},
            11: {(CANCEL_MOVEMENT, None)},
            78: {(CANCEL_MOVEMENT, None)},
            1: {(ALLOW_INPUT_RH_ATTACK, None)},
            25: {(ALLOW_INPUT_DODGE, None)},
            87: {(ALLOW_INPUT_RH_ATTACK, None), (ALLOW_INPUT_DODGE, None)},
        }
        for jid, needed in want.items():
            label = ', '.join(f'{hex(va)}({edx})' if edx is not None else hex(va)
                              for va, edx in sorted(needed, key=str))
            check(f'JumpTable {jid} case calls {label}',
                  needed <= calls[jid], True, src)
        check('JumpTable 115 does not open R2', (SET_ALLOWED_CANCEL, ACT_R2) in calls[115],
              False, src)
        early = _early_jump_table_ids(DEOBF_1162)
        check('type-300 handles cancel ids 4, 11, 26, 115, 116',
              {4, 11, 26, 115, 116} <= early, True, 'EXE jump table 0x140426348/0x14042639c')
        check('early type 2 reads PlayerIns vtable +0x400 (GetWeaponWeightRate)',
              _early_value_case(DEOBF_1162, EARLY_WEAPON_WEIGHT_RATE),
              b'\x48\xff\xa0\x00\x04\x00\x00',  # rex.w jmp qword ptr [rax + 0x400]
              'EXE jump table 0x14042f9f0')
    else:
        skips.append('EXE recovery ids: ' + DEOBF_1162 + ' absent')

    # 8. The same ids named by the community TimeAct template (authored apart from this tool).
    if os.path.exists(TAE_TEMPLATE_ER):
        names = _template_jump_table_names(TAE_TEMPLATE_ER)
        src = 'COMMUNITY: WitchyBND TAE.Template.ER.xml'
        for jid, name in ((1, 'Input - RH Attack'), (4, 'Cancel - RH Attack'),
                          (11, 'Cancel - LS Movement'), (25, 'Input - Dodge'),
                          (26, 'Cancel - Dodge'), (87, 'Input - Common'),
                          (115, 'Cancel - R1 Attack'), (116, 'Cancel - R2 Attack')):
            check(f'template names JumpTable {jid}', names.get(jid), f'{jid}: {name}', src)
    else:
        skips.append('community TAE template: ' + TAE_TEMPLATE_ER + ' absent')

    # 9. The clip-length reader against the dagger R1 clip's own frame fields.
    path = hkx_path(20, 30000)
    if path is None:
        skips.append('hkx length: ' + PLAYER_HKX_ROOT + ' has no a020_030000.hkx')
    else:
        clip = hkx_duration(path)
        check('a020_030000.hkx is 41 frames at 1/30 s', clip and (clip[1], round(clip[2], 6)),
              (41, round(1 / 30, 6)), 'hkx hkaSplineCompressedAnimation')

    # 10. The atkAttribute resolver in the installed build's image: 253 reads the weapon row at
    #     +0x104 and 252 at +0x191. Which fields sit there comes from the paramdef layout, not
    #     from this module's WEAPON_ATTRIBUTE_REF, and the Smithbox enum names them independently.
    if os.path.exists(DEOBF_1171):
        src = 'EXE 0x1406868e0 in eldenring-deobf-1.17.1.bin'
        code = _image_read(DEOBF_1171, 0x1406868e0, 0x100)
        for off, want, text in ((0xcd, '80f9fd', 'cmp cl, 0xfd'),
                                (0xd7, '0fb68804010000', 'movzx ecx, byte [rax+0x104]'),
                                (0xe0, '80f9fc', 'cmp cl, 0xfc'),
                                (0xea, '0fb68891010000', 'movzx ecx, byte [rax+0x191]')):
            check(f'resolver +{off:#x} is {text}', code[off:off + len(want) // 2].hex(), want, src)
    else:
        skips.append('atkAttribute resolver: ' + DEOBF_1171 + ' absent')
    fields, _ = PR.layout(PR.paramdef('EQUIP_PARAM_WEAPON_ST'))
    at = {f['off']: f['name'] for f in fields if not f['bits']}
    check('EquipParamWeapon +0x104 / +0x191', (at.get(0x104), at.get(0x191)),
          ('atkAttribute', 'atkAttribute2'), 'Smithbox paramdef layout')
    for field, value in ((WEAPON_ATTRIBUTE_REF[253], 'atkAttribute'),
                         (WEAPON_ATTRIBUTE_REF[252], 'atkAttribute2')):
        check(f'resolver map sends to {value}', field, value, 'EXE resolver (above)')
    enum = os.path.join(os.path.dirname(PR.PARAMDEF_DIR), 'Param Enums', 'ATKPARAM_ATKATTR_TYPE.json')
    if os.path.exists(enum):
        with open(enum, encoding='utf-8-sig') as handle:
            opts = {o['Key']: o['Names'][0]['Text'] for o in json.load(handle)['Options']}
        src = 'COMMUNITY: Smithbox ATKPARAM_ATKATTR_TYPE enum'
        check('enum 252', opts.get('252'), 'EquipParamWeapon atkAttribute2 reference', src)
        check('enum 253', opts.get('253'), 'EquipParamWeapon atkAttribute reference', src)
    else:
        skips.append('atkAttribute enum: ' + enum + ' absent')

    # 11. Counter frames. The regulation's stateInfo-110 rows are what the EXE buckets as the
    #     counter factor (image byte check), and Smithbox names the TAE's row "Counter Frames".
    if os.path.exists(DEOBF_1171):
        check('CalculateDefenseModifiers compares stateInfo 0x6e',
              _image_read(DEOBF_1171, 0x1404f631e, 4).hex(), '6683f86e',
              'EXE 0x1404f631e in eldenring-deobf-1.17.1.bin')
    names = PR.row_names('SpEffectParam')
    check('SpEffect 45 row name', names.get(45), '[HKS] Counter Frames',
          'COMMUNITY: Smithbox Param Row Names, SpEffectParam')
    if tae_animations(23) is not None:
        gs = next(r for r in weapon_attacks(reg, 4000000) if r['slot'] == 'r1_1')
        check('Greatsword R1 applies SpEffect 45 during its swing',
              [c['speffect'] for c in gs['counter_windows']], [45], 'TAE a23.tae event 66')

    # 12. Play speed. a026_030000 carries one TAE 608 window, 1.34 over clip frames 0-17, so its
    #     clip-frame-22 hit lands 17 / 1.34 + 5 real frames in. The expected values are
    #     arithmetic on the decoded window; the gradient case is checked against the closed form
    #     and against er-mechanics-reach's numeric integral (a different method).
    if tae_animations(26) is None or tae_animations(27) is None:
        skips.append('TAE 608 conversion: a26/a27 absent')
    else:
        src = 'TAE a26.tae event 608 + arithmetic'
        gs = next(r for r in weapon_attacks(reg, 4000000) if r['slot'] == 'r1_1')
        check('Greatsword R1 608 window', gs['speed_windows'], [(0, 17, 1.34, 1.34)], src)
        check('Greatsword R1 hit, clip frames', gs['hit_windows_clip'], [(22, 26)], src)
        check('Greatsword R1 hit, real frames', gs['hit_windows'],
              [(round(17 / 1.34 + 5, 1), round(17 / 1.34 + 9, 1))], src)
        check('Greatsword R1 roll cancel is clip - (17 - 17 / 1.34) real',
              gs['cancel_frame']['dodge'],
              round(gs['cancel_frame_clip']['dodge'] - 17 + 17 / 1.34, 1), src)
        events = tae_animations(27)[31230]
        to_real = clip_to_real(events)
        want = 3 + 6 * math.log(0.8 / 0.6) / 0.2 + 4 + 3 / 1.3 + 4  # windows 3-9 (0.6 -> 0.8), 13-16 (1.3)
        check('a027_031230 clip frame 20 -> real (closed form)',
              round(to_real(20 / TAE_FPS) * TAE_FPS, 2), round(want, 2),
              'TAE a27.tae event 608 + arithmetic')
        numeric = _reach().real_seconds(20 / TAE_FPS, _reach().speed_windows(events))
        check('a027_031230 clip frame 20 -> real (reach numeric integral, 0.02 f)',
              abs(to_real(20 / TAE_FPS) - numeric) * TAE_FPS < 0.02, True,
              'scripts/er-mechanics-reach.py real_seconds')

    # 13. Imported animations. Fire Knight's Greatsword (spAtkcategory 263) has no R1 events of
    #     its own: a263_030000's mini-header imports a137_030000. The expected windows are read
    #     straight from a137's entry here, not through resolve_events.
    if tae_animations(263) is None or tae_animations(137) is None:
        skips.append('imported animation: a263/a137 absent')
    else:
        src = 'TAE a263.tae mini-header + a137.tae'
        fk = reg.find_weapon("Fire Knight's Greatsword")
        check('a263_030000 has no events of its own', tae_animations(263)[30000], [], src)
        row = next((r for r in weapon_attacks(reg, fk) if r['slot'] == 'r1_1'), None)
        check("Fire Knight's Greatsword R1 row exists, read from a137_030000",
              row and (row['tae_entry'], row['anim'], row['imported']),
              ('a263_030000', 'a137_030000', True), src)
        direct = [(clip_frame(e.start), clip_frame(e.end)) for e in tae_animations(137)[30000]
                  if e.type == TAE_ATTACK_BEHAVIOR and struct.unpack_from('<i', e.params, 8)[0] == 0
                  and not struct.unpack_from('<H', e.params, ATTACK_STATE_GATE_OFFSET)[0]]
        check("Fire Knight's Greatsword R1 hit frames = a137_030000's", row and row['hit_windows_clip'],
              direct, src)

    # 14. Gated hitboxes: the count of type-1 events with a nonzero Args+0xe across every player
    #     TAE, measured here, and the Lance R1 extras that carry it.
    if tae_animations(37) is None:
        skips.append('gated hitboxes: a37 absent')
    else:
        gated, states = 0, set()
        for name in sorted(os.listdir(PLAYER_TAE_DIR)):
            stem = name[1:-4]
            if not (name.startswith('a') and name.endswith('.tae') and stem.isdigit()):
                continue
            for evs in (tae_animations(int(stem)) or {}).values():
                for e in evs:
                    if e.type == TAE_ATTACK_BEHAVIOR:
                        gate = struct.unpack_from('<H', e.params, ATTACK_STATE_GATE_OFFSET)[0]
                        if gate:
                            gated += 1
                            states.add(gate)
        src = 'TAE a*.tae type 1 Args+0xe'
        check('gated type-1 events in the player TAEs', (gated, sorted(states)), (189, [187]), src)
        lance = next(r for r in weapon_attacks(reg, reg.find_weapon('Lance')) if r['slot'] == 'r1_1')
        check('Lance R1 keeps no judge 5000-5005 extra', [o['judge'] for o in lance['other_hitboxes']
                                                         if 5000 <= o['judge'] <= 5005], [], src)

    # 15. The a023 right-hand fallback of FUN_1403f1d40: in motion_category, not in the slot
    #     rows (see weapon_attacks). In the
    #     image: hand 1 (`cmp esi, 1`) adds 23000000 (`lea edi, [rax+0x15ef3c0]`), hand 0 adds
    #     48000000 (`lea edi, [rax+0x2dc6c00]`). 1.17.1 has the function 0x230 higher (the two
    #     `lea` encodings each occur once in that image).
    for build, path, shift in (('1.16.2', DEOBF_1162, 0), ('1.17.1', DEOBF_1171, 0x230)):
        if not os.path.exists(path):
            skips.append(f'FUN_1403f1d40 {build}: {path} absent')
            continue
        src = f'EXE {hex(0x1403f1d40 + shift)} in {os.path.basename(path)}'
        check(f'{build} right hand falls back to 23000000 + anim',
              (_image_read(path, 0x1403f1eb2 + shift, 3).hex(),
               _image_read(path, 0x1403f1ee1 + shift, 6).hex()), ('83fe01', '8db8c0f35e01'), src)
        check(f'{build} left hand falls back to 48000000 + anim',
              _image_read(path, 0x1403f1ea7 + shift, 6).hex(), '8db8006cdc02', src)
    if tae_animations(23) is None or tae_animations(31) is None:
        skips.append('a023 fallback: a23/a31 absent')
    else:
        src = 'TAE entry lists a23/a26/a31'
        gc = reg.find_weapon('Giant-Crusher')
        check('a031 has no 032310, a023 does', (_bound(31, 32310), _bound(23, 32310)),
              (False, True), src)
        check('motion_category follows the EXE to a023 for Giant-Crusher 032310',
              motion_category(reg.weapon[gc], 32310), 23, 'EXE order (above) + ' + src)
        rows = {r['slot']: r for r in weapon_attacks(reg, gc, 'both')}
        check('Giant-Crusher 2H crouch R1 row stays on the rolling R1 a031_032300',
              (rows['2h_crouch_r1']['anim'], rows['2h_crouch_r1'].get('crouch_fallback')),
              ('a031_032300', 'rolling R1'), src)
        gs2 = {r['slot']: r for r in weapon_attacks(reg, 4000000, 'both')}
        check('Greatsword 2H crouch R1 keeps its own a026_032310', gs2['2h_crouch_r1']['anim'],
              'a026_032310', src)
        hks = 'c0000.hks IsUseStealthAttack'
        check('IsUseStealthAttack: Giant-Crusher no, Greatsword yes',
              (uses_stealth_attack(reg.weapon[gc], 'both'),
               uses_stealth_attack(reg.weapon[4000000], 'both')), (False, True), hks)
        falx = reg.find_weapon('Falx')
        check('IsUseStealthAttack: Falx (sp 255) one-handed yes, two-handed no',
              (uses_stealth_attack(reg.weapon[falx], 'one'),
               uses_stealth_attack(reg.weapon[falx], 'both')), (True, False), hks)
        fx2 = {r['slot']: r for r in weapon_attacks(reg, falx, 'both')}
        check('Falx 2H crouch R1 is its rolling R1 although a028_032310 exists',
              (fx2['2h_crouch_r1']['tae_entry'], fx2['2h_crouch_r1'].get('crouch_fallback')),
              (fx2['2h_roll_r1']['tae_entry'], 'rolling R1'), hks)
        # The powerstance gate: Greatsword passes the one-hand gate and fails this one; a spear
        # fails the one-hand gate and passes this one.
        check('IsUseStealthAttack(TRUE): Giant-Crusher, Greatsword, Spear, Longsword',
              tuple(uses_dual_stealth_attack(reg.weapon[reg.find_weapon(n)])
                    for n in ('Giant-Crusher', 'Greatsword', 'Spear', 'Longsword')),
              (False, False, True, True), hks + '(TRUE)')
        check('034310 exists in exactly the dual-gate TAEs plus 39',
              {c for c in range(20, 60) if 34310 in (tae_animations(c) or {})},
              set(DUAL_STEALTH_ATTACK_CATEGORIES) | {39}, 'TAE')
        bow = reg.find_weapon('Longbow')
        r1 = next((r for r in weapon_attacks(reg, bow) if r['slot'] == 'r1_1'), None)
        check('Longbow R1 does not take a023_030000', r1 and r1['tae_entry'], 'a044_030000', src)

    # 16. Hit records. The EXE sites HIT_RECORD_NOTE rests on, as bytes in the 1.16.2 image,
    #     then the walk on synthetic events and on Milady's running R1 (a060_030200: judge 998 on
    #     index 1 and 123 on index 0 over clip f16-18, then 120 on index 0 over f18-19).
    if os.path.exists(DEOBF_1162):
        src = 'EXE eldenring-deobf.bin (1.16.2)'
        for va, want, text in ((0x14044299d, '807b0400', 'cmp byte [rbx+4], 0 (index claimed)'),
                               (0x1404429f6, '397038', 'cmp [rax+0x38], esi (same behavior id)'),
                               (0x140442a6f, 'c703ffffffff', 'mov dword [rbx], -1 before create'),
                               (0x140523638, '0f57d2', 'xorps xmm2, xmm2 (record lifetime 0)')):
            check(f'{hex(va)} is {text}', _image_read(DEOBF_1162, va, len(want) // 2).hex(), want, src)
    else:
        skips.append('hit-record EXE sites: ' + DEOBF_1162 + ' absent')
    f = 1 / TAE_FPS
    src = 'HIT_RECORD_NOTE rule, synthetic events'
    check('same judge, same index, back to back: one record',
          hit_records([(0, 2 * f, 7, 0), (2 * f, 4 * f, 7, 0)]), [(1, None), (0, None)], src)
    check('same judge after a one-frame gap: a new record',
          hit_records([(0, 2 * f, 7, 0), (3 * f, 4 * f, 7, 0)]), [(1, None), (1, None)], src)
    check('different judge takes over the index: a new record',
          hit_records([(0, 2 * f, 7, 0), (2 * f, 3 * f, 8, 0)]), [(1, None), (1, 7)], src)
    check('overlap on one index: the first event holds it, the second only after',
          hit_records([(0, 3 * f, 7, 0), (1 * f, 2 * f, 8, 0)]), [(1, None), (0, None)], src)
    check('co-timed events on two indices: one record each',
          hit_records([(0, 2 * f, 7, 0), (0, 2 * f, 8, 1)]), [(1, None), (1, None)], src)
    if tae_animations(60) is None:
        skips.append('Milady running R1: a60 absent')
    else:
        src = 'TAE a60.tae a060_030200 + AtkParam_Pc opposeTarget'
        run = next(r for r in weapon_attacks(reg, reg.find_weapon('Milady')) if r['slot'] == 'run_r1')
        check('Milady running R1 extras (judge, index, can hit enemy, hits)',
              [(o['judge'], o['attack_index'], o['can_hit_enemy'], o['hits'])
               for o in run['other_hitboxes']], [(998, 1, False, 0), (123, 0, True, 1)], src)
        check('Milady running R1 judge 120 replaces 123 on index 0',
              [(d['attack_index'], d['follows_judge'], d['hits']) for d in run['hit_window_detail']],
              [(0, 123, 1)], src)
        check('Milady running R1 max_hits / sweep_hits', (run['max_hits'], run['sweep_hits']),
              (2, 1), src)
        check('Milady running R1 sweep hit is judge 123',
              [o['judge'] for o in run['other_hitboxes'] if o['sweep_hit']]
              + [judge for d in run['hit_window_detail'] if d['sweep_hit']
                 for judge in (run['judge'],)], [123], src)

    for line in passes:
        print('PASS', line)
    for line in skips:
        print('SKIP', line)
    for line in failures:
        print('FAIL', line)
    print(f'{len(passes)} passed, {len(failures)} failed, {len(skips)} skipped')
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('weapon', nargs='?', help='EquipParamWeapon id or name')
    ap.add_argument('--grip', choices=('one', 'both'), default='one')
    ap.add_argument('--level', type=int, default=0, help='reinforce level (stamina damage)')
    ap.add_argument('--regulation')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.weapon:
        ap.error('weapon required')
    reg = Regulation(a.regulation)
    wid = reg.find_weapon(a.weapon)
    rows = weapon_attacks(reg, wid, a.grip, a.level)
    if a.json:
        print(json.dumps({'weapon': wid, 'name': reg.weapon_names.get(wid),
                          'attacks': rows}, indent=1))
    else:
        print_table(reg, wid, rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
