//! Every `eldenring.exe` function the DLL calls, by offset from the module base (Windows loads the
//! game at a random base), with the first eight bytes Elden Ring 2.7.2.0 has there. The DLL checks
//! all of them before it calls any, and does nothing on a mismatch or on any other build.
//!
//! The offsets and bytes are 2.7.2.0's, read out of `eldenring-deobf-1.17.2.bin`. They were first
//! measured on 1.17.1 (bd er-effects-rs-gpkk). Two kinds of site deserve a note:
//!
//! - `audio manager` is `lea rcx,[rip+disp]`, reaching the global at `0x48543e0`.
//! - the four `AK` sites are a `jmp` into an Arxan stub, and the stub is regenerated per build.
//!   `GetEventIDFromPlayingID` has no `.pdata` record; its one caller is at `0x22173b2`.

pub struct Site {
    pub name: &'static str,
    /// Offset on 2.7.2.0.
    pub rva: usize,
    /// First eight bytes on 2.7.2.0.
    pub bytes: [u8; 8],
    /// How many of the eight bytes identify the site. An Arxan stub entry is a five-byte
    /// `jmp rel32`; the bytes after an unconditional jump are filler, and on 2.7.2.0 the running
    /// process has different filler there than the de-Arxan'd image (`GetPositioningInfo`: live
    /// `c8 aa ce`, image `48 8d 65`, the `jmp` itself identical). The `jmp` names its target, so it
    /// is the whole identity.
    pub len: usize,
}

impl Site {
    /// Whether `got` is this site, comparing only the identifying bytes.
    pub fn matches(&self, got: &[u8; 8]) -> bool {
        got[..self.len] == self.bytes[..self.len]
    }
}

const fn site(name: &'static str, rva: usize, bytes: [u8; 8]) -> Site {
    Site {
        name,
        rva,
        bytes,
        len: 8,
    }
}

/// An Arxan stub entry: compared on its five-byte `jmp rel32` only (see [`Site::len`]).
const fn site_arxan_jmp(name: &'static str, rva: usize, bytes: [u8; 8]) -> Site {
    assert!(bytes[0] == 0xE9);
    Site {
        name,
        rva,
        bytes,
        len: 5,
    }
}

pub const ALLOC: usize = 0x01EB_D430;
pub const MO_AUDIO_INPUT_CTOR: usize = 0x0221_A1E0;
pub const MO_GAME_OBJECT_SET_NAME: usize = 0x0221_8FE0;
pub const AUDIO_FORMAT: usize = 0x0221_9F50;
pub const EVENT_NAME: usize = 0x0221_29B0;
pub const MO_AUDIO_INPUT_INIT: usize = 0x0221_A520;
pub const AUDIO_MANAGER: usize = 0x0222_F1C0;
pub const AUDIO_MANAGER_REGISTER: usize = 0x0222_F430;
pub const AUDIO_INPUT_ENTITY: usize = 0x0222_E160;
pub const MO_GAME_OBJECT_SET_POSITION: usize = 0x0221_9010;
pub const MO_AUDIO_INPUT_PLAY: usize = 0x0221_A5F0;
pub const MO_AUDIO_INPUT_PUSH: usize = 0x0221_A670;
pub const MO_AUDIO_INPUT_STOP: usize = 0x0221_A630;
pub const AK_LOAD_BANK_MEMORY_COPY: usize = 0x0223_D560;
pub const AK_GET_EVENT_ID_FROM_PLAYING_ID: usize = 0x0222_4640;
pub const AK_GET_POSITIONING_INFO: usize = 0x0222_4E90;
pub const AK_UNLOAD_BANK: usize = 0x0224_4410;
pub const GET_PLAYER_INS_BY_STEAM_ID: usize = 0x0050_93F0;

/// The two allocator globals the voice object is allocated from: the second when set, else the
/// first (as the game's own audio input setup chooses). They hold heap pointers, so there are no
/// bytes to compare; the eleven rip-relative references to them include `0x22177ac` and
/// `0x2220dca`.
pub const ALLOCATOR_PRIMARY: usize = 0x0485_3F50;
pub const ALLOCATOR_OVERRIDE: usize = 0x0485_3F70;

/// The audio input entity's offset of the Wwise playing id, written by Play.
pub const ENTITY_PLAYING_ID: usize = 0x130;
/// `MOAudioInputObject` size and its entity id field.
pub const MO_AUDIO_INPUT_SIZE: usize = 0x118;
pub const MO_AUDIO_INPUT_ENTITY_ID: usize = 0x114;

pub const SITES: [Site; 18] = [
    site(
        "alloc",
        ALLOC,
        [0x49, 0x8B, 0x00, 0x4D, 0x8B, 0xC8, 0x4C, 0x8B],
    ),
    site(
        "MOAudioInputObject ctor",
        MO_AUDIO_INPUT_CTOR,
        [0x48, 0x89, 0x4C, 0x24, 0x08, 0x57, 0x48, 0x83],
    ),
    site(
        "MOGameObject set name",
        MO_GAME_OBJECT_SET_NAME,
        [0x48, 0x83, 0xC1, 0x20, 0xE9, 0x37, 0x93, 0xFF],
    ),
    site(
        "audio format",
        AUDIO_FORMAT,
        [0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x74],
    ),
    site(
        "event name",
        EVENT_NAME,
        [0x48, 0x89, 0x5C, 0x24, 0x10, 0x48, 0x89, 0x74],
    ),
    site(
        "MOAudioInputObject init",
        MO_AUDIO_INPUT_INIT,
        [0x40, 0x57, 0x48, 0x83, 0xEC, 0x40, 0x48, 0xC7],
    ),
    site(
        "audio manager",
        AUDIO_MANAGER,
        [0x48, 0x8D, 0x0D, 0x19, 0x52, 0x62, 0x02, 0x48],
    ),
    site(
        "audio manager register",
        AUDIO_MANAGER_REGISTER,
        [0x40, 0x57, 0x48, 0x83, 0xEC, 0x40, 0x48, 0xC7],
    ),
    site(
        "audio input entity",
        AUDIO_INPUT_ENTITY,
        [0x85, 0xD2, 0x74, 0x3A, 0x81, 0xFA, 0xFF, 0xFF],
    ),
    site(
        "MOGameObject set position",
        MO_GAME_OBJECT_SET_POSITION,
        [0x48, 0x89, 0x5C, 0x24, 0x08, 0x57, 0x48, 0x83],
    ),
    site(
        "MOAudioInputObject play",
        MO_AUDIO_INPUT_PLAY,
        [0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x48, 0x8B],
    ),
    site(
        "MOAudioInputObject push",
        MO_AUDIO_INPUT_PUSH,
        [0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x74],
    ),
    site(
        "MOAudioInputObject stop",
        MO_AUDIO_INPUT_STOP,
        [0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x48, 0x8B],
    ),
    site_arxan_jmp(
        "AK LoadBankMemoryCopy",
        AK_LOAD_BANK_MEMORY_COPY,
        [0xE9, 0x77, 0x36, 0x19, 0xFE, 0x48, 0x8B, 0x8A],
    ),
    site_arxan_jmp(
        "AK GetEventIDFromPlayingID",
        AK_GET_EVENT_ID_FROM_PLAYING_ID,
        [0xE9, 0xC4, 0xFA, 0x8A, 0xFE, 0xCC, 0x0F, 0xB7],
    ),
    site_arxan_jmp(
        "AK GetPositioningInfo",
        AK_GET_POSITIONING_INFO,
        [0xE9, 0x0F, 0xDC, 0x98, 0xFE, 0x48, 0x8D, 0x65],
    ),
    site_arxan_jmp(
        "AK UnloadBank",
        AK_UNLOAD_BANK,
        [0xE9, 0x82, 0x00, 0x5C, 0xFF, 0x48, 0x8D, 0x8A],
    ),
    site(
        "GetPlayerInsBySteamId",
        GET_PLAYER_INS_BY_STEAM_ID,
        [0x48, 0x89, 0x5C, 0x24, 0x10, 0x48, 0x89, 0x6C],
    ),
];

/// The first site whose bytes differ from what 2.7.2.0 has there, as `(site, what was read)`.
/// `read` is handed the site's offset and answers `None` for an unreadable address.
pub fn first_mismatch(
    mut read: impl FnMut(usize) -> Option<[u8; 8]>,
) -> Option<(&'static Site, Option<[u8; 8]>)> {
    SITES.iter().find_map(|s| {
        let got = read(s.rva);
        (!got.is_some_and(|g| s.matches(&g))).then_some((s, got))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image() -> impl FnMut(usize) -> Option<[u8; 8]> {
        move |rva| SITES.iter().find(|s| s.rva == rva).map(|s| s.bytes)
    }

    /// The live 2.7.2.0 process has `e9 0f dc 98 fe c8 aa ce` at `GetPositioningInfo`: the
    /// image's `jmp` and different filler after it. That is the same stub entry.
    #[test]
    fn an_arxan_stub_matches_on_its_jmp_alone() {
        let live = [0xE9, 0x0F, 0xDC, 0x98, 0xFE, 0xC8, 0xAA, 0xCE];
        let mut real = image();
        assert!(
            first_mismatch(|rva| if rva == AK_GET_POSITIONING_INFO {
                Some(live)
            } else {
                real(rva)
            })
            .is_none()
        );
        // A different jump target is a different stub.
        let mut real = image();
        let other = [0xE9, 0x10, 0xDC, 0x98, 0xFE, 0x48, 0x8D, 0x65];
        assert_eq!(
            first_mismatch(|rva| if rva == AK_GET_POSITIONING_INFO {
                Some(other)
            } else {
                real(rva)
            })
            .unwrap()
            .0
            .rva,
            AK_GET_POSITIONING_INFO
        );
    }

    #[test]
    fn all_match_or_first_named() {
        assert!(first_mismatch(image()).is_none());
        let mut real = image();
        let (s, got) = first_mismatch(|rva| {
            if rva == AUDIO_FORMAT {
                Some([0; 8])
            } else {
                real(rva)
            }
        })
        .unwrap();
        assert_eq!(s.rva, AUDIO_FORMAT);
        assert_eq!(got, Some([0; 8]));
        assert_eq!(first_mismatch(|_| None).unwrap().0.rva, ALLOC);
    }
}
