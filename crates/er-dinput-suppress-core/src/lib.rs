//! Keeping a click on an imgui overlay from also being a weapon swing.
//!
//! # Why this is a crate and not a module
//!
//! ELDEN RING reads the mouse through DirectInput, which never touches the window procedure, so
//! hudhook's `MessageFilter` -- a method on the `ImguiRenderLoop` trait, and therefore available
//! only to whichever DLL won the overlay host mutex -- closes just the legacy-message half of the
//! hole. The half that matters is blanking `rgbButtons[0]` in the `GetDeviceState` buffer while
//! the pointer sits over a panel, and that half needs no host status at all: it is one chained
//! handler on a vtable entry.
//!
//! The logic lived in `er-net-effects`, which is `crate-type = ["cdylib"]` and therefore cannot
//! be depended on -- visibility was never the obstacle, the target kind was. A guest overlay in
//! another DLL (`er-invasion-warp`'s settings panel) needs the same 18 lines, so they moved here.
//!
//! # Why a per-DLL static is the right shape here
//!
//! [`set_pointer_over_overlay`] writes a plain `static`, so every DLL linking this crate gets its
//! own copy -- which is usually the bug (imgui's context is a per-DLL global and that is exactly
//! why a guest must adopt the host's). Here it is the feature. `er-net-effects` stores `false`
//! every frame its bar is not hovered; one bool shared between two DLLs would be last-writer-wins
//! and the click would reach the game about half the time. Separate bools plus `er-hook`'s
//! chaining union give or semantics instead: each DLL blanks for its own rect, and the game sees
//! the button cleared if any overlay owns the pointer.

//!
//! # The keyboard half
//!
//! The same shape for keys an overlay is navigated with: [`set_keys_taken`] names DIK scan codes,
//! and `install_keyboard_suppression` chains one handler onto the keyboard's `GetDeviceState`
//! that zeroes those bytes in a 256-byte read. A key taken while down stays blanked until it is
//! released, so the key that closes an overlay does not also reach the game.

pub mod dinput_state;

#[cfg(windows)]
mod install;

#[cfg(windows)]
pub use install::{
    install_keyboard_suppression, install_mouse_suppression, keyboard_hook_fires, mouse_hook_fires,
    suppressed_mouse_clicks,
};

use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};

/// DIK scan codes this module's overlay is taking from the game right now, one bit per code.
/// Per-DLL, like [`POINTER_OVER_OVERLAY`], so two overlays each take their own keys.
static KEYS_TAKEN: [AtomicU64; 4] = [const { AtomicU64::new(0) }; 4];
/// Keys that were taken while down and have not been seen released since. A key stays blanked
/// until it is released, so the Enter that confirmed a choice does not also reach the game the
/// frame the overlay closes.
static KEYS_HELD: [AtomicU64; 4] = [const { AtomicU64::new(0) }; 4];
/// Key-down reads this module blanked.
static SUPPRESSED_KEYS: AtomicUsize = AtomicUsize::new(0);

fn key_bit(dik: u8) -> (usize, u64) {
    (usize::from(dik) / 64, 1u64 << (dik % 64))
}

/// Take these DIK scan codes from the game, replacing the previous set; an empty slice gives every
/// key back (a key still held stays blanked until it is released).
pub fn set_keys_taken(keys: &[u8]) {
    let mut words = [0u64; 4];
    for &dik in keys {
        let (word, bit) = key_bit(dik);
        words[word] |= bit;
    }
    for (slot, word) in KEYS_TAKEN.iter().zip(words) {
        slot.store(word, Ordering::Relaxed);
    }
}

/// Blank the taken keys in a DirectInput keyboard read: a 256-byte DIK table whose byte per key
/// has bit 7 set while it is down. Any other buffer shape is left alone.
///
/// # Safety
///
/// `data` must be the state buffer DirectInput just filled, valid for `size` bytes.
pub unsafe fn blank_taken_keys(hr: i32, size: u32, data: *mut u8) {
    if hr < 0 || data.is_null() || !dinput_state::is_keyboard_state(size) {
        return;
    }
    for word in 0..4 {
        let taken = KEYS_TAKEN[word].load(Ordering::Relaxed);
        let held = KEYS_HELD[word].load(Ordering::Relaxed);
        let mut watch = taken | held;
        if watch == 0 {
            continue;
        }
        let mut still_held = 0u64;
        while watch != 0 {
            let bit = watch.trailing_zeros() as usize;
            watch &= watch - 1;
            let key = unsafe { data.add(word * 64 + bit) };
            if unsafe { *key } & 0x80 != 0 {
                unsafe { *key = 0 };
                still_held |= 1 << bit;
                SUPPRESSED_KEYS.fetch_add(1, Ordering::Relaxed);
            }
        }
        KEYS_HELD[word].store(still_held, Ordering::Relaxed);
    }
}

/// How many key-down reads this module has blanked.
#[must_use]
pub fn suppressed_keys() -> usize {
    SUPPRESSED_KEYS.load(Ordering::Relaxed)
}

/// Set while this module's overlay owns the pointer. Per-DLL by design; see the module docs.
static POINTER_OVER_OVERLAY: AtomicBool = AtomicBool::new(false);

/// Mouse reads whose left button this module blanked.
static SUPPRESSED_MOUSE_CLICKS: AtomicUsize = AtomicUsize::new(0);

/// Publish whether this module's overlay currently owns the mouse pointer.
///
/// Call it every frame with the hit-test result, `false` included: a panel that stops drawing
/// owns no pointer and may not go on swallowing clicks. Clearing it from the game thread as well
/// as the render thread means a stalled render loop cannot leave the left button blanked for
/// good.
pub fn set_pointer_over_overlay(over: bool) {
    POINTER_OVER_OVERLAY.store(over, Ordering::Relaxed);
}

/// Does this module's overlay own the pointer right now?
#[must_use]
pub fn pointer_over_overlay() -> bool {
    POINTER_OVER_OVERLAY.load(Ordering::Relaxed)
}

/// Blank the left mouse button in a DirectInput mouse read while this module's overlay owns the
/// pointer.
///
/// The click still reaches imgui -- hudhook feeds that from the window procedure, which this
/// never touches -- so the panel's button works while the swing it would otherwise trigger does
/// not.
///
/// Exposed as a free function, not only as part of the installed handler, because a caller that
/// already owns a `GetDeviceState` detour for its own reasons (`er-net-effects` hooks the
/// keyboard, and the mouse arrives at the same entry whenever both devices share a vtable) must
/// be able to run the same blanking without registering a second handler.
///
/// # Safety
///
/// `data` must be the state buffer DirectInput just filled, valid for `size` bytes.
pub unsafe fn blank_overlay_mouse_click(hr: i32, size: u32, data: *mut u8) {
    if hr < 0 || data.is_null() || !POINTER_OVER_OVERLAY.load(Ordering::Relaxed) {
        return;
    }
    if !dinput_state::is_mouse_state(size) {
        return;
    }
    let button = unsafe { data.add(dinput_state::MOUSE_BUTTON0_OFFSET) };
    if unsafe { *button } & 0x80 == 0 {
        return;
    }
    unsafe { *button = 0 };
    SUPPRESSED_MOUSE_CLICKS.fetch_add(1, Ordering::Relaxed);
}

/// How many clicks this module has kept out of the game.
#[must_use]
pub fn suppressed_clicks() -> usize {
    SUPPRESSED_MOUSE_CLICKS.load(Ordering::Relaxed)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `POINTER_OVER_OVERLAY` and the counter are process-global, which is the whole point of the
    /// design and a hazard for a test harness that runs these threads in parallel. Every test
    /// that touches either takes this first, so one test's `false` cannot land inside another's
    /// blanking.
    static SERIALIZE: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// The buffer a DirectInput mouse read fills: three `LONG` axes then `rgbButtons`.
    fn mouse_buffer(pressed: bool) -> [u8; dinput_state::MOUSE_STATE_BYTES] {
        let mut buf = [0u8; dinput_state::MOUSE_STATE_BYTES];
        buf[dinput_state::MOUSE_BUTTON0_OFFSET] = if pressed { 0x80 } else { 0 };
        buf
    }

    #[test]
    fn a_click_is_blanked_only_while_the_pointer_is_over_the_overlay() {
        let _serialized = SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        let mut buf = mouse_buffer(true);
        set_pointer_over_overlay(false);
        unsafe {
            blank_overlay_mouse_click(0, dinput_state::MOUSE_STATE_BYTES as u32, buf.as_mut_ptr());
        }
        assert_eq!(
            buf[dinput_state::MOUSE_BUTTON0_OFFSET],
            0x80,
            "a click outside the panel is the player's swing and must reach the game"
        );

        set_pointer_over_overlay(true);
        unsafe {
            blank_overlay_mouse_click(0, dinput_state::MOUSE_STATE_BYTES as u32, buf.as_mut_ptr());
        }
        assert_eq!(buf[dinput_state::MOUSE_BUTTON0_OFFSET], 0);
        set_pointer_over_overlay(false);
    }

    #[test]
    fn a_keyboard_read_through_a_shared_vtable_is_left_alone() {
        let _serialized = SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        // The case that made this predicate exact rather than a lower bound: both devices can
        // resolve to one `GetDeviceState`, so a keyboard read arrives here too. Byte 12 of the
        // DIK table is a scancode, not a mouse button.
        set_pointer_over_overlay(true);
        let mut dik = [0x80u8; dinput_state::KEYBOARD_STATE_BYTES];
        unsafe {
            blank_overlay_mouse_click(
                0,
                dinput_state::KEYBOARD_STATE_BYTES as u32,
                dik.as_mut_ptr(),
            );
        }
        assert!(
            dik.iter().all(|byte| *byte == 0x80),
            "a keyboard buffer must survive the mouse blanking untouched"
        );
        set_pointer_over_overlay(false);
    }

    #[test]
    fn taken_keys_are_blanked_until_released() {
        let _serialized = SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        const DIK_UP: u8 = 0xc8;
        const DIK_W: u8 = 0x11;
        let mut dik = [0u8; dinput_state::KEYBOARD_STATE_BYTES];
        dik[usize::from(DIK_UP)] = 0x80;
        dik[usize::from(DIK_W)] = 0x80;
        set_keys_taken(&[DIK_UP]);
        let before = suppressed_keys();
        unsafe { blank_taken_keys(0, 256, dik.as_mut_ptr()) };
        assert_eq!(dik[usize::from(DIK_UP)], 0, "a taken key is blanked");
        assert_eq!(
            dik[usize::from(DIK_W)],
            0x80,
            "every other key reaches the game"
        );
        assert_eq!(suppressed_keys(), before + 1);

        // Given back while still held: it stays blanked until it is released.
        set_keys_taken(&[]);
        dik[usize::from(DIK_UP)] = 0x80;
        unsafe { blank_taken_keys(0, 256, dik.as_mut_ptr()) };
        assert_eq!(dik[usize::from(DIK_UP)], 0);
        dik[usize::from(DIK_UP)] = 0;
        unsafe { blank_taken_keys(0, 256, dik.as_mut_ptr()) };
        dik[usize::from(DIK_UP)] = 0x80;
        unsafe { blank_taken_keys(0, 256, dik.as_mut_ptr()) };
        assert_eq!(
            dik[usize::from(DIK_UP)],
            0x80,
            "a fresh press after release is the game's"
        );
    }

    #[test]
    fn a_mouse_read_is_not_a_keyboard_read() {
        let _serialized = SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        set_keys_taken(&[0x0c]);
        let mut buf = mouse_buffer(true);
        unsafe { blank_taken_keys(0, dinput_state::MOUSE_STATE_BYTES as u32, buf.as_mut_ptr()) };
        assert_eq!(buf[dinput_state::MOUSE_BUTTON0_OFFSET], 0x80);
        set_keys_taken(&[]);
    }

    #[test]
    fn a_failed_read_is_not_rewritten() {
        let _serialized = SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        set_pointer_over_overlay(true);
        let mut buf = mouse_buffer(true);
        unsafe {
            blank_overlay_mouse_click(-1, dinput_state::MOUSE_STATE_BYTES as u32, buf.as_mut_ptr());
        }
        assert_eq!(
            buf[dinput_state::MOUSE_BUTTON0_OFFSET],
            0x80,
            "a buffer DirectInput did not fill carries no state to blank"
        );
        set_pointer_over_overlay(false);
    }

    #[test]
    fn the_counter_moves_only_on_a_real_suppression() {
        let _serialized = SERIALIZE.lock().unwrap_or_else(|e| e.into_inner());
        set_pointer_over_overlay(true);
        let before = suppressed_clicks();
        let mut released = mouse_buffer(false);
        unsafe {
            blank_overlay_mouse_click(
                0,
                dinput_state::MOUSE_STATE_BYTES as u32,
                released.as_mut_ptr(),
            );
        }
        assert_eq!(
            suppressed_clicks(),
            before,
            "a button that was already up was not suppressed by us"
        );
        let mut pressed = mouse_buffer(true);
        unsafe {
            blank_overlay_mouse_click(
                0,
                dinput_state::MOUSE_STATE_BYTES as u32,
                pressed.as_mut_ptr(),
            );
        }
        assert_eq!(suppressed_clicks(), before + 1);
        set_pointer_over_overlay(false);
    }
}
