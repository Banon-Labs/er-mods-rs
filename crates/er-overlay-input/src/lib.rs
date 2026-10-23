//! Taking the game's input while an in-game overlay menu is open, and giving it back.
//!
//! Four pieces, each the one the game actually reads:
//!
//! * **Cursor and camera.** The game frees and shows the cursor, and stops turning the camera with
//!   the mouse, whenever its "a menu has the mouse" predicate answers true (2.7.2.0 `0x140766b00`;
//!   see `er-npc-summons/src/cursor.rs`, where it was measured live). [`install_cursor`] hooks it
//!   to answer true while the menu takes input. It registers on the elected hub's union through
//!   `er_hook::register_shared_hook_following_arxan`, so one MinHook instance owns the entry, every
//!   DLL hooking the predicate for its own menu chains there, and any one menu open is enough.
//! * **Mouse buttons and wheel.** Read through DirectInput, so `er-dinput-suppress-core` zeroes
//!   every mouse read while the menu takes input. imgui still gets the mouse: hudhook feeds it from
//!   the window procedure.
//! * **Keyboard.** Every DirectInput scancode is blanked in the game's reads, so typing in the menu
//!   does not walk the character. A key held when the menu closes stays blanked until it is
//!   released, so the key that closed it does not also reach the game.
//! * **Pad.** `XInputGetState` for pad 0 comes back with the sticks centred, the triggers up and no
//!   buttons, the same "held until released" rule applying to buttons ([`pad_after`]).
//!
//! # One copy, per-DLL state
//!
//! This was er-cel-shade's `input.rs`; er-render-scale's menu needed the same four pieces, so they
//! moved here. Every DLL linking this crate has its own copy of its statics, so each menu takes and
//! gives back input for itself, and the hub's union chains the handlers: the game sees input taken
//! while any menu takes it. All three hooks go through the cross-image registrars
//! (`register_shared_hook_following_arxan`, `register_shared_hook_with_budget`), which hand them to
//! the elected hub and fall back to this DLL's own union only when no hub is loaded. The handler
//! names (`menu_has_mouse_union`, `xinput_get_state`, and `keyboard_get_state_hook` in
//! `er-dinput-suppress-core`) are the ones the `[[shared]]` rows of `scripts/me3-dll-conflicts.toml`
//! name, so `scripts/check-shared-hook-rvas.py` proves each DLL that links this crate registers them
//! on a union and never on a bare MinHook detour.
//!
//! The cursor predicate went through `register_union_hook_runtime_derived_following_arxan` until
//! 2026-10-22, which is this DLL's own union: one MinHook instance per menu DLL on one entry. That
//! run, three of four read the pristine prologue before any wrote its jump, and only the last to
//! enable was ever called -- er-duel-rules took 82 frames of input with its handler at zero calls.

// The host build compiles the pure half only.
#![cfg_attr(not(windows), allow(dead_code))]

use std::sync::atomic::{AtomicBool, Ordering};

#[cfg(windows)]
mod hooks;

#[cfg(windows)]
pub use hooks::{install_cursor, try_install_dinput, try_install_pad};

/// Where a hook writes a line about itself: the linking DLL's own log.
pub type LogFn = fn(std::fmt::Arguments<'_>);

/// Every DirectInput scancode but zero, which no key produces.
pub const ALL_KEYS: [u8; 255] = {
    let mut keys = [0u8; 255];
    let mut at = 0;
    while at < 255 {
        keys[at] = at as u8 + 1;
        at += 1;
    }
    keys
};

/// Is the menu taking input? Set by [`set_taking`], read by every hook here.
static TAKING: AtomicBool = AtomicBool::new(false);

/// Take the game's input, or give it back.
pub fn set_taking(taking: bool) {
    TAKING.store(taking, Ordering::Release);
    er_dinput_suppress_core::set_mouse_taken(taking);
    er_dinput_suppress_core::set_keys_taken(if taking { &ALL_KEYS } else { &[] });
    if !taking {
        er_dinput_suppress_core::set_pointer_over_overlay(false);
    }
}

/// Is the menu taking the game's input right now?
#[must_use]
pub fn taking() -> bool {
    TAKING.load(Ordering::Acquire)
}

/// What one pad read becomes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PadRead {
    /// The menu takes input: zero the whole gamepad, and remember these buttons as held.
    Zeroed { held: u16 },
    /// The game gets the read, with these buttons, and these stay held until released.
    Passed { buttons: u16, held: u16 },
}

/// The pad rule: while the menu takes input every read is zeroed and its buttons remembered;
/// afterwards a remembered button stays cleared until a read sees it released, so the button that
/// closed the menu does not also reach the game.
#[must_use]
pub const fn pad_after(taking: bool, buttons: u16, held: u16) -> PadRead {
    if taking {
        return PadRead::Zeroed { held: buttons };
    }
    let still = held & buttons;
    PadRead::Passed {
        buttons: buttons & !still,
        held: still,
    }
}

/// What the input capture has done this session, for a telemetry file.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct InputCounters {
    /// The menu takes input now.
    pub taking: bool,
    /// The predicate is hooked, and how many of its calls this DLL answered.
    pub cursor_hooked: bool,
    pub cursor_forced: u64,
    /// The DirectInput keyboard and mouse blanking are armed.
    pub keyboard_armed: bool,
    pub mouse_armed: bool,
    /// Key-down reads and whole mouse reads this DLL blanked.
    pub keys_blanked: u64,
    pub mouse_reads_blanked: u64,
    /// `XInputGetState` is hooked, and how many pad reads this DLL zeroed.
    pub pad_hooked: bool,
    pub pad_zeroed: u64,
}

/// The counters as they are now.
#[must_use]
pub fn counters() -> InputCounters {
    #[cfg(windows)]
    let hooked = hooks::counters();
    #[cfg(not(windows))]
    let hooked = InputCounters::default();
    InputCounters {
        taking: taking(),
        keys_blanked: er_dinput_suppress_core::suppressed_keys() as u64,
        mouse_reads_blanked: er_dinput_suppress_core::suppressed_mouse_reads() as u64,
        ..hooked
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_key_but_zero_is_taken() {
        assert_eq!(ALL_KEYS.len(), 255);
        assert_eq!(ALL_KEYS[0], 1);
        assert_eq!(ALL_KEYS[254], 255);
        assert!(!ALL_KEYS.contains(&0));
    }

    #[test]
    fn a_pad_button_held_across_the_close_stays_cleared_until_released() {
        const A: u16 = 0x1000;
        const B: u16 = 0x2000;
        // Open: everything zeroed, A remembered.
        assert_eq!(pad_after(true, A, 0), PadRead::Zeroed { held: A });
        // Closed with A still down and B newly pressed: A stays cleared, B goes through.
        assert_eq!(
            pad_after(false, A | B, A),
            PadRead::Passed {
                buttons: B,
                held: A
            }
        );
        // A released: nothing held any more.
        assert_eq!(
            pad_after(false, B, A),
            PadRead::Passed {
                buttons: B,
                held: 0
            }
        );
        // Pressed again after the release: it reaches the game.
        assert_eq!(
            pad_after(false, A, 0),
            PadRead::Passed {
                buttons: A,
                held: 0
            }
        );
    }

    #[test]
    fn taking_and_giving_back_is_reported() {
        set_taking(true);
        assert!(taking());
        assert!(counters().taking);
        set_taking(false);
        assert!(!taking());
        assert!(!counters().taking);
    }
}
