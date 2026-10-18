//! The Wwise bank that makes `Play_VoiceChat` play the Audio Input source plugin at a game object,
//! with the falloff, bus and environment sends of the game's own NPC dialogue.
//!
//! This is `scripts/er-voice-bank.py` revision 2 (branch `feat/voice-audio-probes`), the bank the
//! Cheat Engine table loaded in the sessions where positional voice was heard. [`build`] writes
//! the same 479 bytes; the test pins their length and FNV-1a 64.
//!
//! What goes in, and where each value comes from:
//!
//! - `BKHD`: generator version 135 (Wwise 2019.2), language SFX, project 0x772, alignment 16, the
//!   values every extracted non-localised game bank carries.
//! - `CAkAttenuation` `0x13310CAC`: the attenuation of the game's NPC dialogue (10,146 sounds;
//!   every `vc*.bnk` carries the same object under the same id). It is written here from its
//!   curve points ([`ATTENUATION_CURVES`]) rather than copied out of `enus/vcmain.bnk`.
//! - `CAkFxCustom` with id = the sound's source id, holding the Audio Input parameters (fGain).
//!   The engine finds a source plugin's parameters only there (`CAkSrcFxBase` start, 1.17.1
//!   `0x1423ba2c0`); without it every voice of the sound ended within 300 ms.
//! - `CAkSound`: source plugin `0x00C80002` (Audiokinetic Audio Input, registered statically in
//!   `eldenring.exe`), output bus `0x6F005C01` (the bus of 8,811 NPC dialogue sounds), the props
//!   in [`PROPS`], 3D emitter positioning with attenuation, game-defined aux sends on.
//! - Play and Stop actions on that sound, and the events `Play_VoiceChat` / `Stop_VoiceChat`.

// How Wwise turns a name into an id: FNV-1 32 of the lowercase name.
use er_game_base::fnv1a::fnv1_32_ascii_lowercase as wwise_id;

pub const BANK_VERSION: u32 = 135;
pub const LANGUAGE_SFX: u32 = 0x1770_5D3E;
pub const PROJECT_ID: u32 = 0x772;
pub const ALIGNMENT: u16 = 16;

pub const AUDIO_INPUT_PLUGIN: u32 = 0x00C8_0002;
pub const ATTENUATION_ID: u32 = 0x1331_0CAC;
pub const OUTPUT_BUS: u32 = 0x6F00_5C01;

/// Bumped whenever the bank's content changes. It is part of the sound's id, so the loader can ask
/// Wwise whether this revision is the one loaded and reload only when it is not.
pub const REVISION: u32 = 2;

pub const BANK_ID: u32 = wwise_id(b"er_voicechat");
pub const SOUND_ID: u32 = wwise_id(b"er_voicechat_audio_input_sound_r2");
pub const SOURCE_ID: u32 = wwise_id(b"er_voicechat_audio_input_source");
pub const PLAY_ACTION_ID: u32 = wwise_id(b"er_voicechat_play_action");
pub const STOP_ACTION_ID: u32 = wwise_id(b"er_voicechat_stop_action");
pub const PLAY_EVENT_ID: u32 = wwise_id(b"Play_VoiceChat");
pub const STOP_EVENT_ID: u32 = wwise_id(b"Stop_VoiceChat");

/// The event names the DLL hands `MOAudioInputObject::Init`, NUL-terminated.
pub const PLAY_EVENT_NAME: &[u8] = b"Play_VoiceChat\0";
pub const STOP_EVENT_NAME: &[u8] = b"Stop_VoiceChat\0";

const ACTION_PLAY: u16 = 0x0403;
const ACTION_STOP_E: u16 = 0x0103;

const HIRC_SOUND: u8 = 2;
const HIRC_ACTION: u8 = 3;
const HIRC_EVENT: u8 = 4;
const HIRC_ATTENUATION: u8 = 14;
const HIRC_FX_CUSTOM: u8 = 0x11;

/// A sound property value: Wwise stores each as four bytes, float or id.
#[derive(Clone, Copy)]
pub enum Prop {
    Float(f32),
    Id(u32),
}

/// AkPropID values for bank version 135, ascending as the game's banks order them: Volume +9 dB,
/// MakeUpGain +6 dB (the highest the game's own sounds carry; 0 dB was reported far too quiet),
/// Priority 80, CenterPCT 50, GameAuxSendVolume -12 dB, AttenuationID.
pub const PROPS: [(u8, Prop); 6] = [
    (0, Prop::Float(9.0)),
    (6, Prop::Float(6.0)),
    (7, Prop::Float(80.0)),
    (14, Prop::Float(50.0)),
    (23, Prop::Float(-12.0)),
    (70, Prop::Id(ATTENUATION_ID)),
];

/// One attenuation curve point: distance, value, interpolation (`AkCurveInterpolation`). The
/// floats are bit patterns so the curve is the game's exactly.
pub struct CurvePoint {
    pub from: u32,
    pub to: u32,
    pub interp: u32,
}

/// One attenuation curve: its scaling (2 = dB, 0 = none) and points.
pub struct Curve {
    pub scaling: u8,
    pub points: &'static [CurvePoint],
}

const fn pt(from: u32, to: u32, interp: u32) -> CurvePoint {
    CurvePoint { from, to, interp }
}

/// Which curve each attenuation channel uses, -1 for none: volume dry, volume wet (aux), LPF,
/// HPF, spread, focus, and one more unused.
pub const ATTENUATION_CURVE_USE: [i8; 7] = [0, 1, 0, 2, 3, 4, -1];

/// The dialogue attenuation's five curves, distances 0..50 m.
pub const ATTENUATION_CURVES: [Curve; 5] = [
    Curve {
        scaling: 2,
        points: &[
            pt(0x0000_0000, 0xBE80_0DDE, 4),
            pt(0x4040_0000, 0xBEE0_14CC, 8),
            pt(0x41A0_0000, 0xBF1A_15A6, 7),
            pt(0x4232_BAEC, 0xBF80_0000, 9),
            pt(0x4248_0000, 0xBF80_0000, 4),
        ],
    },
    Curve {
        scaling: 2,
        points: &[
            pt(0x0000_0000, 0x0000_0000, 4),
            pt(0x41A0_0000, 0xBEA9_FE84, 8),
            pt(0x4248_0000, 0xBF80_0000, 4),
        ],
    },
    Curve {
        scaling: 0,
        points: &[
            pt(0x0000_0000, 0x0000_0000, 7),
            pt(0x40A0_0000, 0x41A0_0000, 4),
            pt(0x41A0_0000, 0x420C_0000, 4),
            pt(0x4248_0000, 0x4220_0000, 4),
        ],
    },
    Curve {
        scaling: 0,
        points: &[
            pt(0x0000_0000, 0x4120_0000, 0),
            pt(0x4040_0000, 0x41B8_0000, 4),
            pt(0x4248_0000, 0x41C8_0000, 4),
        ],
    },
    Curve {
        scaling: 0,
        points: &[
            pt(0x0000_0000, 0x4220_0000, 4),
            pt(0x4040_0000, 0x4149_8000, 4),
            pt(0x4248_0000, 0x4100_0000, 4),
        ],
    },
];

struct W(Vec<u8>);

impl W {
    fn u8(&mut self, v: u8) -> &mut Self {
        self.0.push(v);
        self
    }
    fn u16(&mut self, v: u16) -> &mut Self {
        self.0.extend_from_slice(&v.to_le_bytes());
        self
    }
    fn u32(&mut self, v: u32) -> &mut Self {
        self.0.extend_from_slice(&v.to_le_bytes());
        self
    }
    fn f32(&mut self, v: f32) -> &mut Self {
        self.u32(v.to_bits())
    }
    /// Wwise's 7-bit variable-length int. Every count written here is below 0x80, one byte.
    fn var(&mut self, v: u8) -> &mut Self {
        debug_assert!(v < 0x80);
        self.u8(v)
    }
}

fn attenuation_body() -> Vec<u8> {
    let mut w = W(Vec::new());
    w.u32(ATTENUATION_ID).u8(0); // no cone
    for c in ATTENUATION_CURVE_USE {
        w.u8(c.cast_unsigned());
    }
    w.u8(ATTENUATION_CURVES.len() as u8);
    for curve in &ATTENUATION_CURVES {
        w.u8(curve.scaling).u16(curve.points.len() as u16);
        for p in curve.points {
            w.u32(p.from).u32(p.to).u32(p.interp);
        }
    }
    w.u16(0); // no RTPC
    w.0
}

fn fx_custom_body() -> Vec<u8> {
    let mut w = W(Vec::new());
    w.u32(SOURCE_ID).u32(AUDIO_INPUT_PLUGIN).u32(4).f32(0.0); // fGain 0 dB
    w.u8(0).u16(0).var(0).var(0).u16(0);
    w.0
}

fn sound_body() -> Vec<u8> {
    let mut w = W(Vec::new());
    w.u32(SOUND_ID);
    // AkBankSourceData: plugin, stream type 0 (in bank), media info, empty inline params (the
    // params are the CAkFxCustom above).
    w.u32(AUDIO_INPUT_PLUGIN)
        .u8(0)
        .u32(SOURCE_ID)
        .u32(0)
        .u8(0)
        .u32(0);
    // NodeBaseParams: no effects, no attachment override, output bus, no parent.
    w.u8(0).u8(0).u8(0).u32(OUTPUT_BUS).u32(0).u8(1);
    w.u8(PROPS.len() as u8);
    for (id, _) in PROPS {
        w.u8(id);
    }
    for (_, v) in PROPS {
        match v {
            Prop::Float(f) => w.f32(f),
            Prop::Id(i) => w.u32(i),
        };
    }
    w.u8(0); // ranged modifiers
    w.u8(0x03).u8(0x0A); // positioning: override, 3D; position + orientation, attenuation on
    w.u8(0x03).u32(0); // aux: override, game-defined sends; no user aux
    w.u8(0).u8(1).u16(0).u8(0).u8(0); // advanced settings as the game's dialogue
    w.var(0).var(0).u16(0); // no states, no RTPC
    w.0
}

fn action_body(id: u32, kind: u16) -> Vec<u8> {
    let mut w = W(Vec::new());
    w.u32(id).u16(kind).u32(SOUND_ID).u8(0).u8(0).u8(0).u8(0x04);
    if kind == ACTION_PLAY {
        w.u32(BANK_ID);
    } else {
        w.u8(0x06).var(0);
    }
    w.0
}

fn event_body(id: u32, action: u32) -> Vec<u8> {
    let mut w = W(Vec::new());
    w.u32(id).var(1).u32(action);
    w.0
}

/// The whole bank, ready for `AK::SoundEngine::LoadBankMemoryCopy`.
pub fn build() -> Vec<u8> {
    let items: [(u8, Vec<u8>); 7] = [
        (HIRC_ATTENUATION, attenuation_body()),
        (HIRC_FX_CUSTOM, fx_custom_body()),
        (HIRC_SOUND, sound_body()),
        (HIRC_ACTION, action_body(PLAY_ACTION_ID, ACTION_PLAY)),
        (HIRC_ACTION, action_body(STOP_ACTION_ID, ACTION_STOP_E)),
        (HIRC_EVENT, event_body(PLAY_EVENT_ID, PLAY_ACTION_ID)),
        (HIRC_EVENT, event_body(STOP_EVENT_ID, STOP_ACTION_ID)),
    ];
    let mut hirc = W(Vec::new());
    hirc.u32(items.len() as u32);
    for (kind, body) in &items {
        hirc.u8(*kind).u32(body.len() as u32);
        hirc.0.extend_from_slice(body);
    }
    let mut bkhd = W(Vec::new());
    bkhd.u32(BANK_VERSION)
        .u32(BANK_ID)
        .u32(LANGUAGE_SFX)
        .u16(ALIGNMENT)
        .u16(0)
        .u32(PROJECT_ID);
    let mut out = W(Vec::new());
    out.0.extend_from_slice(b"BKHD");
    out.u32(bkhd.0.len() as u32);
    out.0.extend_from_slice(&bkhd.0);
    out.0.extend_from_slice(b"HIRC");
    out.u32(hirc.0.len() as u32);
    out.0.extend_from_slice(&hirc.0);
    out.0
}

#[cfg(test)]
mod tests {
    use super::*;
    use er_game_base::fnv1a::fnv1a64 as fnv1a_64;

    #[test]
    fn ids_match_seamless_and_the_table() {
        assert_eq!(PLAY_EVENT_ID, 0xFDF5_AFAA);
        assert_eq!(STOP_EVENT_ID, 0xD072_BE04);
        assert_eq!(BANK_ID, 0x279B_79E9);
        assert_eq!(SOUND_ID, 0x1B18_0742);
    }

    /// The bank the Cheat Engine table loaded in the sessions where positional voice was heard
    /// (`er-voice-chat.CT`, 2026-10-17): 479 bytes, FNV-1a 64 `0x68870601ef1fa962`.
    #[test]
    fn bank_is_the_one_heard_in_game() {
        let bank = build();
        assert_eq!(bank.len(), 479);
        assert_eq!(fnv1a_64(&bank), 0x6887_0601_ef1f_a962);
    }

    /// Against the game's own copy of the attenuation, when the extracted sound banks are on
    /// this machine (`ER_SD_ROOT`, default `~/er-extract/LOOK_HERE_ALL_ASSETS_20260713/sd`).
    #[test]
    fn attenuation_matches_vcmain_when_present() {
        let root = std::env::var_os("ER_SD_ROOT")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME").map(|h| {
                    std::path::PathBuf::from(h).join("er-extract/LOOK_HERE_ALL_ASSETS_20260713/sd")
                })
            });
        let Some(bytes) = root.and_then(|r| std::fs::read(r.join("enus/vcmain.bnk")).ok()) else {
            return;
        };
        let ours = attenuation_body();
        let mut off = 0;
        while off + 8 <= bytes.len() {
            let size = u32::from_le_bytes(bytes[off + 4..off + 8].try_into().unwrap()) as usize;
            if &bytes[off..off + 4] == b"HIRC" {
                let count = u32::from_le_bytes(bytes[off + 8..off + 12].try_into().unwrap());
                let mut p = off + 12;
                for _ in 0..count {
                    let kind = bytes[p];
                    let n = u32::from_le_bytes(bytes[p + 1..p + 5].try_into().unwrap()) as usize;
                    let body = &bytes[p + 5..p + 5 + n];
                    if kind == HIRC_ATTENUATION && body[..4] == ATTENUATION_ID.to_le_bytes() {
                        assert_eq!(body, &ours[..]);
                        return;
                    }
                    p += 5 + n;
                }
            }
            off += 8 + size;
        }
        panic!("vcmain.bnk has no attenuation 0x13310CAC");
    }
}
