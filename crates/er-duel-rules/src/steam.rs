//! The local Steam name, read through the flat exports of the `steam_api64.dll` the game loads for
//! its own multiplayer. The rule-break list prints it beside the local player's character, so a
//! file read later says which account broke the rule (`er_duel_rules_core::feed::player_label`).

#![cfg(windows)]

use std::ffi::{c_char, c_void};

use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::core::{s, w};

/// `SteamAPI_SteamFriends_v017() -> ISteamFriends*` (steam_api_flat.h).
type SteamFriends = unsafe extern "C" fn() -> *mut c_void;
/// `SteamAPI_ISteamFriends_GetPersonaName(ISteamFriends*) -> const char*`, UTF-8.
type GetPersonaName = unsafe extern "C" fn(*mut c_void) -> *const c_char;

/// The local persona name, or `None` while Steam is not up in this process.
pub(crate) fn persona_name() -> Option<String> {
    // SAFETY: a lookup of a module the game loaded; no reference to it is kept.
    let module = unsafe { GetModuleHandleW(w!("steam_api64.dll")) }.ok()?;
    // SAFETY: export lookups on that module.
    let friends = unsafe { GetProcAddress(module, s!("SteamAPI_SteamFriends_v017")) }?;
    // SAFETY: as above.
    let persona = unsafe { GetProcAddress(module, s!("SteamAPI_ISteamFriends_GetPersonaName")) }?;
    // SAFETY: both exports have the flat-API signatures named on the two types above.
    let (friends, persona) = unsafe {
        (
            std::mem::transmute::<unsafe extern "system" fn() -> isize, SteamFriends>(friends),
            std::mem::transmute::<unsafe extern "system" fn() -> isize, GetPersonaName>(persona),
        )
    };
    // SAFETY: a getter that returns null before SteamAPI_Init.
    let interface = unsafe { friends() };
    if interface.is_null() {
        return None;
    }
    // SAFETY: a live ISteamFriends.
    let name = unsafe { persona(interface) };
    // The string is Steam's, not ours, so it is walked through `ReadProcessMemory` rather than
    // trusted to be mapped and terminated. A Steam persona name is at most 128 bytes of UTF-8
    // (k_cwchPersonaNameMax), so 256 leaves room and still bounds the walk.
    // SAFETY: `safe_read_cstr` has no precondition on the address.
    let bytes = unsafe { er_game_base::mem::safe_read_cstr(name as usize, PERSONA_NAME_MAX) }?;
    let name = String::from_utf8(bytes).ok()?;
    let name = name.trim();
    (!name.is_empty()).then(|| name.to_owned())
}

/// Upper bound on the persona name walk, past Steam's own 128-byte limit.
const PERSONA_NAME_MAX: usize = 256;
