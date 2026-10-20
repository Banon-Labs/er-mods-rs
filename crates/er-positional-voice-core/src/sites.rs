//! Every `eldenring.exe` function the DLL calls, by offset from the module base (Windows loads the
//! game at a random base), with the first eight bytes Elden Ring 1.17.1 has there. The DLL checks
//! all of them before it calls any, and does nothing on a mismatch.
//!
//! The offsets are 1.17.1's. On 2.7.2.0 the DLL carries each one with
//! `er_game_base::game_build::carry_1171_rva` and compares the bytes 2.7.2.0 has at the carried
//! address, [`Site::bytes_1172`]. Thirteen sites hold the same eight bytes on both builds; the other
//! five start with a rel32 operand whose displacement moved with the code around it. Measured in
//! `eldenring-deobf-1.17.1.bin` / `eldenring-deobf-1.17.2.bin` with
//! `scripts/compare-carry-bytes-1172.py` (bd er-effects-rs-gpkk):
//!
//! - `audio manager` is `lea rcx,[rip+disp]`; both builds' operands reach the same global
//!   (1.17.1 `0x48513c0`, 2.7.2.0 `0x48543e0`, which is that global's carry).
//! - the four `AK` sites are a `jmp` into an Arxan stub, and the stub is regenerated per build. Each
//!   is a `.pdata` entry of the same length on both builds (`0xf5`, `0x74`, `0xd3`), except
//!   `GetEventIDFromPlayingID`, which has no `.pdata` record: its one caller in 1.17.1
//!   (`0x2215c82`) carries to its one caller in 2.7.2.0 (`0x22173b2`).

/// The game build whose bytes are compared.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Build {
    /// 1.17.1, FileVersion 2.7.1.0 -- the build the offsets were measured on.
    V1171,
    /// FileVersion 2.7.2.0, reached through the 1.17.1 -> 2.7.2.0 carry.
    V1172,
}

pub struct Site {
    pub name: &'static str,
    /// Offset on 1.17.1.
    pub rva: usize,
    /// First eight bytes on 1.17.1.
    pub bytes: [u8; 8],
    /// First eight bytes at the carried offset on 2.7.2.0.
    pub bytes_1172: [u8; 8],
}

impl Site {
    /// The bytes `build` has at this site.
    pub fn expected(&self, build: Build) -> [u8; 8] {
        match build {
            Build::V1171 => self.bytes,
            Build::V1172 => self.bytes_1172,
        }
    }
}

const fn site(name: &'static str, rva: usize, bytes: [u8; 8]) -> Site {
    Site {
        name,
        rva,
        bytes,
        bytes_1172: bytes,
    }
}

/// A site whose first eight bytes differ on 2.7.2.0; see the module comment for why each does.
const fn site_moved(name: &'static str, rva: usize, bytes: [u8; 8], bytes_1172: [u8; 8]) -> Site {
    Site {
        name,
        rva,
        bytes,
        bytes_1172,
    }
}

pub const ALLOC: usize = 0x01EB_BD40;
pub const MO_AUDIO_INPUT_CTOR: usize = 0x0221_8AB0;
pub const MO_GAME_OBJECT_SET_NAME: usize = 0x0221_78B0;
pub const AUDIO_FORMAT: usize = 0x0221_8820;
pub const EVENT_NAME: usize = 0x0221_1280;
pub const MO_AUDIO_INPUT_INIT: usize = 0x0221_8DF0;
pub const AUDIO_MANAGER: usize = 0x0222_DA90;
pub const AUDIO_MANAGER_REGISTER: usize = 0x0222_DD00;
pub const AUDIO_INPUT_ENTITY: usize = 0x0222_CA30;
pub const MO_GAME_OBJECT_SET_POSITION: usize = 0x0221_78E0;
pub const MO_AUDIO_INPUT_PLAY: usize = 0x0221_8EC0;
pub const MO_AUDIO_INPUT_PUSH: usize = 0x0221_8F40;
pub const MO_AUDIO_INPUT_STOP: usize = 0x0221_8F00;
pub const AK_LOAD_BANK_MEMORY_COPY: usize = 0x0223_BE30;
pub const AK_GET_EVENT_ID_FROM_PLAYING_ID: usize = 0x0222_2F10;
pub const AK_GET_POSITIONING_INFO: usize = 0x0222_3760;
pub const AK_UNLOAD_BANK: usize = 0x0224_2CE0;
pub const GET_PLAYER_INS_BY_STEAM_ID: usize = 0x0050_9170;

/// The two allocator globals the voice object is allocated from: the second when set, else the
/// first (as the game's own audio input setup chooses).
pub const ALLOCATOR_PRIMARY: usize = 0x0485_0F30;
pub const ALLOCATOR_OVERRIDE: usize = 0x0485_0F50;

/// The globals read rather than called. They hold heap pointers, so there are no bytes to compare;
/// the DLL only requires that each carries. On 2.7.2.0 they are at `0x4853f50` and `0x4853f70`:
/// the eleven rip-relative references to them in 1.17.1 (`0x221607c`, `0x221f69a`, ...) carry to
/// the eleven references in 2.7.2.0 (`0x22177ac`, `0x2220dca`, ...).
pub const GLOBALS: [(&str, usize); 2] = [
    ("allocator", ALLOCATOR_PRIMARY),
    ("allocator override", ALLOCATOR_OVERRIDE),
];

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
    site_moved(
        "audio manager",
        AUDIO_MANAGER,
        [0x48, 0x8D, 0x0D, 0x29, 0x39, 0x62, 0x02, 0x48],
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
    site_moved(
        "AK LoadBankMemoryCopy",
        AK_LOAD_BANK_MEMORY_COPY,
        [0xE9, 0x60, 0xF5, 0xB9, 0xFF, 0xF3, 0x44, 0x0F],
        [0xE9, 0x77, 0x36, 0x19, 0xFE, 0x48, 0x8B, 0x8A],
    ),
    site_moved(
        "AK GetEventIDFromPlayingID",
        AK_GET_EVENT_ID_FROM_PLAYING_ID,
        [0xE9, 0x80, 0x43, 0x23, 0xFE, 0x90, 0x2C, 0xE9],
        [0xE9, 0xC4, 0xFA, 0x8A, 0xFE, 0xCC, 0x0F, 0xB7],
    ),
    site_moved(
        "AK GetPositioningInfo",
        AK_GET_POSITIONING_INFO,
        [0xE9, 0x85, 0xB5, 0xA1, 0xFE, 0x48, 0x83, 0xC4],
        [0xE9, 0x0F, 0xDC, 0x98, 0xFE, 0x48, 0x8D, 0x65],
    ),
    site_moved(
        "AK UnloadBank",
        AK_UNLOAD_BANK,
        [0xE9, 0x0A, 0xCC, 0x9F, 0xFE, 0x4C, 0x8B, 0x4C],
        [0xE9, 0x82, 0x00, 0x5C, 0xFF, 0x48, 0x8D, 0x8A],
    ),
    site(
        "GetPlayerInsBySteamId",
        GET_PLAYER_INS_BY_STEAM_ID,
        [0x48, 0x89, 0x5C, 0x24, 0x10, 0x48, 0x89, 0x6C],
    ),
];

/// The first site whose bytes differ from what `build` has there, as `(site, what was read)`.
/// `read` is handed the site's 1.17.1 offset and answers `None` for an unreadable address or one
/// that does not carry to the running build.
pub fn first_mismatch(
    build: Build,
    mut read: impl FnMut(usize) -> Option<[u8; 8]>,
) -> Option<(&'static Site, Option<[u8; 8]>)> {
    SITES.iter().find_map(|s| {
        let got = read(s.rva);
        (got != Some(s.expected(build))).then_some((s, got))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image(build: Build) -> impl FnMut(usize) -> Option<[u8; 8]> {
        move |rva| {
            SITES
                .iter()
                .find(|s| s.rva == rva)
                .map(|s| s.expected(build))
        }
    }

    #[test]
    fn all_match_or_first_named() {
        for build in [Build::V1171, Build::V1172] {
            assert!(first_mismatch(build, image(build)).is_none());
            let mut real = image(build);
            let (s, got) = first_mismatch(build, |rva| {
                if rva == AUDIO_FORMAT {
                    Some([0; 8])
                } else {
                    real(rva)
                }
            })
            .unwrap();
            assert_eq!(s.rva, AUDIO_FORMAT);
            assert_eq!(got, Some([0; 8]));
            assert_eq!(first_mismatch(build, |_| None).unwrap().0.rva, ALLOC);
        }
    }

    /// A 1.17.1 image does not pass as 2.7.2.0, nor the reverse: the five moved sites are what
    /// tell the two apart, so the first of them is named.
    #[test]
    fn one_build_does_not_pass_for_the_other() {
        assert_eq!(
            first_mismatch(Build::V1172, image(Build::V1171))
                .unwrap()
                .0
                .rva,
            AUDIO_MANAGER
        );
        assert_eq!(
            first_mismatch(Build::V1171, image(Build::V1172))
                .unwrap()
                .0
                .rva,
            AUDIO_MANAGER
        );
        let moved = SITES.iter().filter(|s| s.bytes != s.bytes_1172).count();
        assert_eq!(moved, 5);
    }
}
