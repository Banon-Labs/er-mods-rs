//! The voice chat key: flips the game's own Network > Voice Chat setting and says which way it
//! went.
//!
//! The setting is the byte this DLL already obeys (`GameSettings::voice_chat`, read every frame by
//! [`crate::game::voice_settings`]), so the key and the menu are one switch: off means no
//! microphone, nothing sent and nothing played. The byte is written on the game thread, from the
//! `FrameBegin` task, and read back before anything is announced, so the clip names the state the
//! game holds. A toggle made while the options menu is open can be overwritten when that menu
//! confirms its own copy.

#![cfg(windows)]

use std::sync::atomic::{AtomicU8, Ordering};

use er_hotkey_config::keys::{Chord, MODIFIER_CTRL, MODIFIER_SHIFT};
use er_hotkey_config::live::AtomicChord;
use er_positional_voice_core::announce::Announce;
use er_positional_voice_core::config::Config;
use windows::Win32::System::Threading::GetCurrentProcessId;
use windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use crate::log::voice_log;

/// The bound key; a chord with `vk == 0` is no key.
static KEY: AtomicChord = AtomicChord::unset();
/// The announcement language, as `Announce::as_u8`.
static ANNOUNCE: AtomicU8 = AtomicU8::new(0);

const NO_KEY: Chord = Chord {
    modifiers: 0,
    vk: 0,
    dik: None,
};

/// Publish the key and language from a freshly read config; the voice thread rereads the file.
pub(crate) fn apply(config: &Config) {
    KEY.store(config.toggle_key.unwrap_or(NO_KEY));
    ANNOUNCE.store(config.announce.as_u8(), Ordering::Relaxed);
}

#[link(name = "winmm")]
unsafe extern "system" {
    fn PlaySoundW(
        sound: *const core::ffi::c_void,
        module: *mut core::ffi::c_void,
        flags: u32,
    ) -> i32;
}

/// `SND_ASYNC | SND_NODEFAULT | SND_MEMORY`: return at once, never fall back to the system beep,
/// and read the WAV from the pointer. A new clip cuts off one still playing.
const PLAY_FLAGS: u32 = 0x0001 | 0x0002 | 0x0004;

fn game_has_focus() -> bool {
    // SAFETY: plain Win32 queries with no pointers kept.
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd.0.is_null() {
        return false;
    }
    let mut pid = 0u32;
    // SAFETY: as above; `pid` is ours.
    unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
    pid == unsafe { GetCurrentProcessId() }
}

/// A press of `chord` since the last frame: down now and up before, or pressed and released in
/// between. Same edge as `er-refill-all`'s keyboard poll.
fn pressed(chord: Chord, was_down: &mut bool) -> bool {
    const HELD: u16 = 0x8000;
    const PRESSED_SINCE_LAST_CALL: u16 = 0x0001;
    const VK_CONTROL: i32 = 0x11;
    const VK_MENU: i32 = 0x12;
    const VK_SHIFT: i32 = 0x10;
    // SAFETY: `GetAsyncKeyState` takes a key code and returns a state.
    let held = |vk: i32| unsafe { GetAsyncKeyState(vk) } as u16 & HELD != 0;
    if (chord.modifiers & MODIFIER_CTRL != 0 && !held(VK_CONTROL))
        || (chord.needs_alt() && !held(VK_MENU))
        || (chord.modifiers & MODIFIER_SHIFT != 0 && !held(VK_SHIFT))
    {
        *was_down = false;
        return false;
    }
    // SAFETY: as above.
    let state = unsafe { GetAsyncKeyState(chord.vk as i32) } as u16;
    let down = state & HELD != 0;
    let edge = (down && !*was_down) || state & PRESSED_SINCE_LAST_CALL != 0;
    *was_down = down;
    edge
}

/// One frame on the game thread: flip the setting on a press. `was_down` is the task's edge latch.
///
/// # Safety
///
/// Game thread.
pub(crate) unsafe fn tick(was_down: &mut bool) {
    let Some(chord) = KEY.load().filter(|c| c.vk != 0) else {
        *was_down = false;
        return;
    };
    // The key state is read even without focus, so the latch tracks it; only a focused press acts.
    if !pressed(chord, was_down) || !game_has_focus() {
        return;
    }
    // SAFETY: game thread.
    let Some(on) = (unsafe { crate::game::flip_voice_chat() }) else {
        voice_log(format_args!(
            "toggle: key pressed before the game settings exist; nothing changed"
        ));
        return;
    };
    voice_log(format_args!(
        "toggle: key pressed, Voice Chat now {}",
        if on { "on" } else { "off" }
    ));
    if let Some(clip) = Announce::from_u8(ANNOUNCE.load(Ordering::Relaxed)).clip(on) {
        // SAFETY: `clip` is a `'static` WAV compiled into this DLL, so it outlives the async play.
        unsafe { PlaySoundW(clip.as_ptr().cast(), core::ptr::null_mut(), PLAY_FLAGS) };
    }
}
