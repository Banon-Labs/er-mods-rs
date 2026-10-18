//! Every `eldenring.exe` function the DLL calls, by offset from the module base (Windows loads the
//! game at a random base), with the first eight bytes Elden Ring 1.17.1 has there. The DLL checks
//! all of them before it calls any, and does nothing on a mismatch.

pub struct Site {
    pub name: &'static str,
    pub rva: usize,
    pub bytes: [u8; 8],
}

const fn site(name: &'static str, rva: usize, bytes: [u8; 8]) -> Site {
    Site { name, rva, bytes }
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
        [0x48, 0x8D, 0x0D, 0x29, 0x39, 0x62, 0x02, 0x48],
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
    site(
        "AK LoadBankMemoryCopy",
        AK_LOAD_BANK_MEMORY_COPY,
        [0xE9, 0x60, 0xF5, 0xB9, 0xFF, 0xF3, 0x44, 0x0F],
    ),
    site(
        "AK GetEventIDFromPlayingID",
        AK_GET_EVENT_ID_FROM_PLAYING_ID,
        [0xE9, 0x80, 0x43, 0x23, 0xFE, 0x90, 0x2C, 0xE9],
    ),
    site(
        "AK GetPositioningInfo",
        AK_GET_POSITIONING_INFO,
        [0xE9, 0x85, 0xB5, 0xA1, 0xFE, 0x48, 0x83, 0xC4],
    ),
    site(
        "AK UnloadBank",
        AK_UNLOAD_BANK,
        [0xE9, 0x0A, 0xCC, 0x9F, 0xFE, 0x4C, 0x8B, 0x4C],
    ),
    site(
        "GetPlayerInsBySteamId",
        GET_PLAYER_INS_BY_STEAM_ID,
        [0x48, 0x89, 0x5C, 0x24, 0x10, 0x48, 0x89, 0x6C],
    ),
];

/// The first site whose bytes differ, as `(site, what was read)`; `read` answers `None` for an
/// unreadable address.
pub fn first_mismatch(
    mut read: impl FnMut(usize) -> Option<[u8; 8]>,
) -> Option<(&'static Site, Option<[u8; 8]>)> {
    SITES.iter().find_map(|s| {
        let got = read(s.rva);
        (got != Some(s.bytes)).then_some((s, got))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_match_or_first_named() {
        assert!(
            first_mismatch(|rva| SITES.iter().find(|s| s.rva == rva).map(|s| s.bytes)).is_none()
        );
        let (s, got) = first_mismatch(|rva| {
            if rva == AUDIO_FORMAT {
                Some([0; 8])
            } else {
                SITES.iter().find(|s| s.rva == rva).map(|s| s.bytes)
            }
        })
        .unwrap();
        assert_eq!(s.rva, AUDIO_FORMAT);
        assert_eq!(got, Some([0; 8]));
        assert_eq!(first_mismatch(|_| None).unwrap().0.rva, ALLOC);
    }
}
