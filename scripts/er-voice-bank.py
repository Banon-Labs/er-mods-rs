#!/usr/bin/env python3
"""Generate er_voicechat.bnk: a Wwise bank for Elden Ring 1.17.1 that makes `Play_VoiceChat` play
the Audio Input source plugin positionally, with the same falloff, bus and environment sends as the
game's own NPC dialogue.

    python3 scripts/er-voice-bank.py                 # write target/voice-bank/er_voicechat.bnk
    python3 scripts/er-voice-bank.py --check         # build in memory and validate, write nothing

What goes in, and where each value comes from:

  BKHD   dwBankGeneratorVersion 135 (Wwise 2019.2), language SFX 0x17705D3E, project 0x772,
         alignment 16 -- the values every extracted non-localised game bank carries.
  HIRC   CAkAttenuation 0x13310CAC, copied byte for byte out of enus/vcmain.bnk. It is the
         attenuation of 10,146 of the game's NPC dialogue sounds (every vc*.bnk carries the same
         object under the same id, so loading it alongside them is what the game's banks do).
         CAkSound: source plugin 0x00C80002 (Audiokinetic Audio Input, registered statically in
         eldenring.exe at 0x143c4d600), output bus 0x6F005C01 (the effective bus of 8,811 NPC
         dialogue sounds), and the properties of the game's NPC voice root actor-mixer 0x15623A42:
         Priority 80, CenterPCT 50, GameAuxSendVolume -12 dB, AttenuationID 0x13310CAC, 3D emitter
         positioning with position+orientation spatialisation and attenuation on, game-defined aux
         sends on (that is how the game's reverb zones reach a sound). The sound has no parent, so
         every value is set on it with its override bit.
         CAkFxCustom with id = the sound's sourceID holding the Audio Input parameters (fGain):
         the engine finds a source plugin's parameters only there, and without it every voice of
         the sound ended at once (playing id gone within 300 ms, the audio input ring never read).
         CAkActionPlay / CAkActionStop (Stop_E) on that sound, laid out like vc201.bnk's, and the
         events Play_VoiceChat 0xFDF5AFAA / Stop_VoiceChat 0xD072BE04 (FNV-1 32 of the lowercase
         name) -- the names Seamless Co-op 2.0.1 and the CE probe post.

Validation: the bank is parsed back with bnnm's wwiser (scripts/er-wwise-dump.py; $WWISER_DIR),
the same parser used to read the game's banks, and every field above is checked on the parse.
"""

import argparse
import os
import pathlib
import struct
import sys
import importlib.util

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / 'target' / 'voice-bank' / 'er_voicechat.bnk'
SD_ROOT = pathlib.Path(os.environ.get('ER_SD_ROOT', pathlib.Path.home() / 'er-extract' / 'LOOK_HERE_ALL_ASSETS_20260713' / 'sd'))
ATTENUATION_SOURCE = SD_ROOT / 'enus' / 'vcmain.bnk'

BANK_VERSION = 135
LANGUAGE_SFX = 0x17705D3E
PROJECT_ID = 0x772
ALIGNMENT = 16

AUDIO_INPUT_PLUGIN = 0x00C80002
ATTENUATION_ID = 0x13310CAC
OUTPUT_BUS = 0x6F005C01

# AkPropID values for bank version 135 (wwiser wdefs AkPropID_128): Volume 0, MakeUpGain 6,
# Priority 7, CenterPCT 14, GameAuxSendVolume 23, AttenuationID 70; ascending, as the game's banks
# order them. Volume +9 dB and MakeUpGain +6 dB are the highest values the game's own CAkSound
# objects carry (surveyed in vcmain, cs_main, cs_smain, init and vc2xx: Volume -6..+9, MakeUpGain
# -3..+6). A decoded Steam voice played through the dialogue bus at 0 dB was reported far too quiet.
# The Audio Input fGain stays 0 dB: its valid range is not read off anything local.
PROPS = [(0, ('f', 9.0)), (6, ('f', 6.0)), (7, ('f', 80.0)), (14, ('f', 50.0)), (23, ('f', -12.0)),
         (70, ('I', ATTENUATION_ID))]

# Bumped whenever the bank's content changes. It is part of the sound's id, so a loader can ask
# Wwise whether this revision is the one loaded (AK Query::GetPositioningInfo answers only for a
# loaded node) and UnloadBank + reload only when it is not; the bank id and event ids never change.
REVISION = 2

ACTION_PLAY = 0x0403
ACTION_STOP_E = 0x0103

HIRC_SOUND, HIRC_ACTION, HIRC_EVENT, HIRC_ATTENUATION, HIRC_FX_CUSTOM = 2, 3, 4, 14, 0x11


def fnv1_32(name: str) -> int:
    h = 0x811C9DC5
    for b in name.lower().encode('ascii'):
        h = (h * 0x01000193) & 0xFFFFFFFF
        h ^= b
    return h


BANK_ID = fnv1_32('er_voicechat')
SOUND_ID = fnv1_32('er_voicechat_audio_input_sound' + (f'_r{REVISION}' if REVISION > 1 else ''))
SOURCE_ID = fnv1_32('er_voicechat_audio_input_source')
PLAY_ACTION_ID = fnv1_32('er_voicechat_play_action')
STOP_ACTION_ID = fnv1_32('er_voicechat_stop_action')
PLAY_EVENT_ID = fnv1_32('Play_VoiceChat')
STOP_EVENT_ID = fnv1_32('Stop_VoiceChat')
assert PLAY_EVENT_ID == 0xFDF5AFAA and STOP_EVENT_ID == 0xD072BE04, 'FNV-1 does not match Seamless'

# --sine-test: the same sound with the Wwise Sine source (0x00640002, default params) instead of
# Audio Input, under its own bank, object ids and events Play_VoiceChatSine/Stop_VoiceChatSine,
# so it loads beside the real bank. It separates "this bank/bus/positioning makes no sound" from
# "the Audio Input source makes no sound".
SINE_PLUGIN = 0x00640002
SOURCE_PLUGIN = AUDIO_INPUT_PLUGIN


PARENT_ID = 0          # DirectParentID of the sound; 0 = top level
ACTION_TARGET = None   # None = our sound; else the play/stop actions target this loaded game node
PLAIN = False          # diagnostic: sound with nothing overridden (see sound_body)


def use_variant(name, source='input', parent=0, target=None, plain=False):
    """A diagnostic bank beside the real one: its own bank id, object ids and events
    Play_VoiceChat<Name>/Stop_VoiceChat<Name>, written to er_voicechat_<name>.bnk."""
    global BANK_ID, SOUND_ID, SOURCE_ID, PLAY_ACTION_ID, STOP_ACTION_ID, PLAY_EVENT_ID, STOP_EVENT_ID
    global SOURCE_PLUGIN, OUT, PARENT_ID, ACTION_TARGET, PLAIN
    PLAIN = plain
    low = name.lower()
    BANK_ID = fnv1_32(f'er_voicechat_{low}')
    SOUND_ID = fnv1_32(f'er_voicechat_{low}_sound')
    SOURCE_ID = fnv1_32(f'er_voicechat_{low}_source')
    PLAY_ACTION_ID = fnv1_32(f'er_voicechat_{low}_play_action')
    STOP_ACTION_ID = fnv1_32(f'er_voicechat_{low}_stop_action')
    PLAY_EVENT_ID = fnv1_32(f'Play_VoiceChat{name}')
    STOP_EVENT_ID = fnv1_32(f'Stop_VoiceChat{name}')
    SOURCE_PLUGIN = SINE_PLUGIN if source == 'sine' else AUDIO_INPUT_PLUGIN
    PARENT_ID = parent
    ACTION_TARGET = target
    OUT = OUT.with_name(f'er_voicechat_{low}.bnk')


def use_sine_test():
    use_variant('Sine', source='sine')


def u8(v): return struct.pack('<B', v)
def u16(v): return struct.pack('<H', v)
def u32(v): return struct.pack('<I', v)
def f32(v): return struct.pack('<f', v)


def var(v):
    """Wwise 7-bit variable-length int (high bit = more bytes follow, big-endian groups)."""
    groups = [v & 0x7F]
    v >>= 7
    while v:
        groups.append((v & 0x7F) | 0x80)
        v >>= 7
    return bytes(reversed(groups))


def hirc_item(kind, body):
    return u8(kind) + u32(len(body)) + body


def raw_hirc_object(bank: bytes, want_id: int, want_type: int) -> bytes:
    off = 0
    while off < len(bank):
        tag, size = bank[off:off + 4], struct.unpack_from('<I', bank, off + 4)[0]
        if tag == b'HIRC':
            count = struct.unpack_from('<I', bank, off + 8)[0]
            p = off + 12
            for _ in range(count):
                kind, n = bank[p], struct.unpack_from('<I', bank, p + 1)[0]
                body = bank[p + 5:p + 5 + n]
                if kind == want_type and struct.unpack_from('<I', body, 0)[0] == want_id:
                    return body
                p += 5 + n
        off += 8 + size
    raise ValueError(f'object 0x{want_id:08X} type {want_type} not in the bank')


def sound_body():
    b = u32(SOUND_ID)
    # AkBankSourceData: plugin, StreamType 0 (in bank), AkMediaInformation, plugin params.
    # The inline parameter block is empty: the engine (CAkSrcFxBase start, 1.17.1 0x1423ba2c0)
    # looks the source's parameters up as a CAkFxCustom whose id is the sourceID, and ends the
    # voice when there is none -- see fx_custom_body.
    b += u32(SOURCE_PLUGIN) + u8(0) + u32(SOURCE_ID) + u32(0) + u8(0) + u32(0)
    # NodeBaseParams
    b += u8(0) + u8(0)                           # NodeInitialFxParams: no override, 0 effects
    b += u8(0)                                   # bOverrideAttachmentParams
    if PLAIN:
        # Diagnostic: laid out exactly like the game's own Silence sources -- nothing overridden,
        # everything (bus, positioning, attenuation) inherited from the parent.
        b += u32(0) + u32(PARENT_ID) + u8(0)
        b += u8(0) + u8(0)                       # no props, no ranged modifiers
        b += u8(0x00)                            # PositioningParams: inherit
        b += u8(0x00) + u32(0)                   # AuxParams: inherit
        b += u8(0) + u8(1) + u16(0) + u8(0) + u8(0)
        b += var(0) + var(0) + u16(0)
        return b
    b += u32(OUTPUT_BUS) + u32(PARENT_ID)        # OverrideBusId, DirectParentID (0 = none)
    b += u8(0x01)                                # bPriorityOverrideParent
    b += u8(len(PROPS)) + bytes(p for p, _ in PROPS)
    b += b''.join(struct.pack('<' + t, v) for _, (t, v) in PROPS)
    b += u8(0)                                   # ranged modifiers: none
    # PositioningParams: override parent + listener-relative routing (3D), panner direct, emitter.
    b += u8(0x03)
    b += u8(0x0A)                                # PositionAndOrientation, bEnableAttenuation
    # AuxParams: override + use game-defined aux sends, no user aux, reflections bus 0.
    b += u8(0x03) + u32(0)
    # AdvSettingsParams, same as the game's dialogue sounds.
    b += u8(0) + u8(1) + u16(0) + u8(0) + u8(0)
    b += var(0) + var(0)                         # StateChunk: no state props, no state groups
    b += u16(0)                                  # InitialRTPC: none
    return b


def fx_custom_body():
    """CAkFxCustom (HIRC 0x11) carrying the source plugin's parameters, id = the sound's sourceID,
    laid out like the game's own (aeg930_103.bnk 0x2432298E, a Wwise Silence source)."""
    params = f32(0.0) if SOURCE_PLUGIN == AUDIO_INPUT_PLUGIN else b''   # fGain 0 dB
    b = u32(SOURCE_ID) + u32(SOURCE_PLUGIN) + u32(len(params)) + params
    b += u8(0)                                   # uNumBankData
    b += u16(0)                                  # InitialRTPC
    b += var(0) + var(0)                         # StateChunk
    b += u16(0)                                  # property values
    return b


def action_body(action_id, action_type):
    target = SOUND_ID if ACTION_TARGET is None else ACTION_TARGET
    b = u32(action_id) + u16(action_type) + u32(target) + u8(0)    # idExt, bIsBus 0
    b += u8(0) + u8(0)                          # prop bundles: none
    b += u8(0x04)                               # eFadeCurve linear
    if action_type == ACTION_PLAY:
        b += u32(BANK_ID)
    else:
        b += u8(0x06) + var(0)                  # stop flags as the game's banks; no exceptions
    return b


def event_body(event_id, action_id):
    return u32(event_id) + var(1) + u32(action_id)


def build() -> bytes:
    attenuation = raw_hirc_object(ATTENUATION_SOURCE.read_bytes(), ATTENUATION_ID, HIRC_ATTENUATION)
    items = [
        hirc_item(HIRC_ATTENUATION, attenuation),
        hirc_item(HIRC_FX_CUSTOM, fx_custom_body()),
        hirc_item(HIRC_SOUND, sound_body()),
        hirc_item(HIRC_ACTION, action_body(PLAY_ACTION_ID, ACTION_PLAY)),
        hirc_item(HIRC_ACTION, action_body(STOP_ACTION_ID, ACTION_STOP_E)),
        hirc_item(HIRC_EVENT, event_body(PLAY_EVENT_ID, PLAY_ACTION_ID)),
        hirc_item(HIRC_EVENT, event_body(STOP_EVENT_ID, STOP_ACTION_ID)),
    ]
    bkhd = u32(BANK_VERSION) + u32(BANK_ID) + u32(LANGUAGE_SFX) + u16(ALIGNMENT) + u16(0) + u32(PROJECT_ID)
    hirc = u32(len(items)) + b''.join(items)
    return b'BKHD' + u32(len(bkhd)) + bkhd + b'HIRC' + u32(len(hirc)) + hirc


def _dump_module():
    spec = importlib.util.spec_from_file_location('er_wwise_dump', HERE / 'er-wwise-dump.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(path: pathlib.Path) -> list:
    """Parse the bank with wwiser and return a list of problems (empty = valid)."""
    d = _dump_module()
    root = d.load(path)
    problems = [f'wwiser error/skip node: {e}' for e in d.errors(root)]

    def val(node, name):
        n = node.find1(name=name)
        return None if n is None else n.get_attr('value')

    if root.get_version() != BANK_VERSION:
        problems.append(f'version {root.get_version()}')
    if root.get_id() != BANK_ID:
        problems.append(f'bank id {root.get_id()}')
    objs = {d.obj_id(o): o for o in d.hirc_objects(root)}
    want = {ATTENUATION_ID: 'CAkAttenuation', SOUND_ID: 'CAkSound', PLAY_ACTION_ID: 'CAkActionPlay',
            STOP_ACTION_ID: 'CAkActionStop', PLAY_EVENT_ID: 'CAkEvent', STOP_EVENT_ID: 'CAkEvent'}
    for oid, kind in want.items():
        if oid not in objs or d.obj_type(objs[oid]) != kind:
            problems.append(f'0x{oid:08X} missing or not {kind}')
    if problems:
        return problems
    s = objs[SOUND_ID]
    checks = {
        'ulPluginID': SOURCE_PLUGIN, 'sourceID': SOURCE_ID, 'OverrideBusId': OUTPUT_BUS,
        'DirectParentID': PARENT_ID, 'bPositioningInfoOverrideParent': 1, 'bHasListenerRelativeRouting': 1,
        'e3DPositionType': 0, 'eSpatializationMode': 2, 'bEnableAttenuation': 1,
    }
    for name, expect in checks.items():
        if val(s, name) != expect:
            problems.append(f'sound {name} = {val(s, name)}, expected {expect}')
    fx = objs.get(SOURCE_ID)
    if fx is None or d.obj_type(fx) != 'CAkFxCustom' or val(fx, 'fxID') != SOURCE_PLUGIN:
        problems.append(f'no CAkFxCustom 0x{SOURCE_ID:08X} for the source plugin')
    elif SOURCE_PLUGIN == AUDIO_INPUT_PLUGIN:
        gain = fx.find1(name='fGain')
        if gain is None or gain.get_attr('value') != 0.0:
            problems.append('CAkFxCustom has no parsed Audio Input fGain')
    aux = s.find1(name='AuxParams').find1(name='byBitVector').get_attr('value')
    if aux & 0x03 != 0x03:
        problems.append(f'aux bits 0x{aux:02X}: game-defined aux sends not on')
    props = dict(zip([n.get_attr('value') for n in s.find1(name='AkPropBundle<AkPropValue,unsigned char>').finds(name='pID')],
                     [n.get_attr('value') for n in s.find1(name='AkPropBundle<AkPropValue,unsigned char>').finds(name='pValue')]))
    for pid, (_, v) in PROPS:
        if props.get(pid) != v:
            problems.append(f'sound prop {pid} = {props.get(pid)}, expected {v}')
    for aid, eid in ((PLAY_ACTION_ID, PLAY_EVENT_ID), (STOP_ACTION_ID, STOP_EVENT_ID)):
        if val(objs[aid], 'idExt') != (SOUND_ID if ACTION_TARGET is None else ACTION_TARGET):
            problems.append(f'action 0x{aid:08X} target {val(objs[aid], "idExt")}')
        if val(objs[eid], 'ulActionID') != aid:
            problems.append(f'event 0x{eid:08X} action {val(objs[eid], "ulActionID")}')
    if val(objs[PLAY_ACTION_ID], 'bankID') != BANK_ID:
        problems.append(f'play action bankID {val(objs[PLAY_ACTION_ID], "bankID")}')
    # The copied attenuation must parse to the same tree as the game's own.
    src = d.load(ATTENUATION_SOURCE)
    game_att = next(o for o in d.hirc_objects(src) if d.obj_id(o) == ATTENUATION_ID)

    def flat(node):
        out = []
        stack = [node]
        while stack:
            n = stack.pop()
            a = n.get_attrs()
            out.append((n.get_nodename(), a.get('name'), a.get('value')))
            stack.extend(reversed(n.get_children() or []))
        return out
    if flat(game_att)[1:] != flat(objs[ATTENUATION_ID])[1:]:
        problems.append('attenuation does not parse identically to vcmain.bnk')
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--out', type=pathlib.Path, default=None)
    ap.add_argument('--check', action='store_true', help='validate without writing the bank')
    ap.add_argument('--sine-test', action='store_true', help='build the Wwise Sine control bank instead')
    ap.add_argument('--variant', default=None,
                    help='diagnostic bank: NAME[,source=sine|input][,parent=0x..][,target=0x..]')
    a = ap.parse_args()
    if a.sine_test:
        use_sine_test()
    if a.variant:
        name, *opts = a.variant.split(',')
        kw = dict(o.split('=', 1) for o in opts)
        use_variant(name, source=kw.get('source', 'input'), parent=int(kw.get('parent', '0'), 0),
                    target=int(kw['target'], 0) if 'target' in kw else None,
                    plain=kw.get('plain') == '1')
    a.out = a.out or OUT
    data = build()
    if a.check:
        tmp = pathlib.Path(os.environ.get('TMPDIR', '/tmp')) / f'er_voicechat.{os.getpid()}.bnk'
        tmp.write_bytes(data)
        try:
            problems = validate(tmp)
        finally:
            tmp.unlink()
    else:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_bytes(data)
        problems = validate(a.out)
    print(f'bank 0x{BANK_ID:08X} v{BANK_VERSION}: {len(data)} bytes; sound 0x{SOUND_ID:08X}, '
          f'Play_VoiceChat 0x{PLAY_EVENT_ID:08X} -> 0x{PLAY_ACTION_ID:08X}, '
          f'Stop_VoiceChat 0x{STOP_EVENT_ID:08X} -> 0x{STOP_ACTION_ID:08X}')
    if problems:
        for p in problems:
            print('INVALID:', p)
        return 1
    print('wwiser parses it cleanly; every checked field matches' + ('' if a.check else f'; wrote {a.out}'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
