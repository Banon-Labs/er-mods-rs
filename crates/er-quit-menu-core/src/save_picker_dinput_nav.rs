//! The save picker's arrow-key navigation, read where the game reads it: DirectInput.
//!
//! # Why this exists a second time
//!
//! The picker's scrolling worked in the product DLL and did not work in a standalone shell, and the
//! difference was never in the pump -- `save_picker_menu_pump_edge_scroll` is the same code in both.
//! It was the input contract underneath. `er-quickload` hooks the keyboard's
//! `IDirectInputDevice8::GetDeviceState` and latches the four arrow scancodes out of the buffer the
//! game itself just read, which gives the pump two distinct things:
//!
//! | what the pump asks for | what a device-state read answers |
//! |---|---|
//! | `nav_held()` | the arrow is down **right now**, on every tick it stays down |
//! | `take_nav_edges_for()` | the tick the arrow **became** down, once per physical press |
//!
//! A shell with no product had neither, so it substituted `CS::MoveDir` -- the engine's resolved
//! menu direction. That answers a different question: it is the direction the menu is acting on
//! this frame, so it pulses with the menu's own auto-repeat and reads as "not held" on every tick
//! between repeats. The pump needs a true hold, because Elden Ring's menus auto-repeat while a
//! direction is down and only the first press produces an edge -- `wrap_nav` refuses to treat the
//! native list's wrap as navigation unless the direction is genuinely held, so under a pulsing
//! source it refuses nearly all of them and the list wraps to the top instead of scrolling.
//!
//! So this is the product's reader, in the crate that owns the rows, available to any shell that
//! links it. `save_picker_native_nav` keeps `CS::MoveDir` only for the case where this reader
//! cannot install.
//!
//! # What it does not cover
//!
//! The pad. The product reads it from `XInputGetState` in the same file this was lifted from, and
//! that half stayed there because it is entangled with the input blocker's fabrication path. A
//! shell therefore navigates the picker by keyboard and mouse; a controller falls through to the
//! `CS::MoveDir` reader and its pulse semantics.

use std::sync::atomic::{AtomicUsize, Ordering};

use er_hook::{MH_STATUS, UnionFn, register_union_hook};
use er_telemetry_core::counters::{DINPUT_KB_GET_STATE_ORIG, DINPUT_KB_HOOK_FIRES};
use windows::Win32::Foundation::HMODULE;
use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress};
use windows::core::{GUID, s};

use crate::host::append_autoload_debug;
use crate::save_picker_menu::{
    SAVE_PICKER_NAV_DOWN_MASK, SAVE_PICKER_NAV_LEFT_MASK, SAVE_PICKER_NAV_RIGHT_MASK,
    SAVE_PICKER_NAV_UP_MASK,
};

/// The four directions this reader answers for. The wheel masks are deliberately absent: the wheel
/// has its own native latch in `save_picker_native_scroll_input`, and two sources for one detent is
/// the double-scroll that latch was written to end.
const DINPUT_NAV_ALL_MASK: usize = SAVE_PICKER_NAV_LEFT_MASK
    | SAVE_PICKER_NAV_RIGHT_MASK
    | SAVE_PICKER_NAV_UP_MASK
    | SAVE_PICKER_NAV_DOWN_MASK;

/// DirectInput keyboard scancodes, and the bit a pressed key sets in the state buffer.
const DIK_UP: usize = 0xc8;
const DIK_LEFT: usize = 0xcb;
const DIK_RIGHT: usize = 0xcd;
const DIK_DOWN: usize = 0xd0;
const DIK_PRESSED: u8 = 0x80;
/// `DIK_DOWN` is the highest scancode read here, so it sets the buffer-length floor.
const DINPUT_KEYBOARD_BUFFER_FLOOR: usize = DIK_DOWN;

/// Directions down on the most recent state read, and the rising edges not yet drained.
static DINPUT_DOWN_MASK: AtomicUsize = AtomicUsize::new(0);
static DINPUT_EDGE_LATCH: AtomicUsize = AtomicUsize::new(0);
static DINPUT_EDGE_COUNT: AtomicUsize = AtomicUsize::new(0);
static DINPUT_NAV_HOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);

const DIRECTINPUT_VERSION: u32 = 0x0800;
const IID_IDIRECTINPUT8W: GUID = GUID::from_values(
    0xbf79_8031,
    0x483a,
    0x4da2,
    [0xaa, 0x99, 0x5d, 0x64, 0xed, 0x36, 0x97, 0x00],
);
const GUID_SYS_KEYBOARD: GUID = GUID::from_values(
    0x6f1d_2b61,
    0xd5a0,
    0x11cf,
    [0xbf, 0xc7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00],
);

/// `IUnknown` and `IDirectInput8` vtable slots, counted from the top of the interface.
const VTBL_RELEASE: usize = 2;
const VTBL_CREATE_DEVICE: usize = 3;
const VTBL_GET_DEVICE_STATE: usize = 9;

type RawObj = *mut *const usize;
type DInput8CreateFn =
    unsafe extern "system" fn(usize, u32, *const GUID, *mut RawObj, usize) -> i32;
type CreateDeviceFn = unsafe extern "system" fn(RawObj, *const GUID, *mut RawObj, usize) -> i32;
type ReleaseFn = unsafe extern "system" fn(RawObj) -> u32;

unsafe fn vtable_fn<F: Copy>(obj: RawObj, slot: usize) -> F {
    unsafe { std::mem::transmute_copy(&*(*obj).add(slot)) }
}

/// Latch the arrow keys out of a keyboard state buffer the game has just been handed.
///
/// Split out and taking a slice so the edge rule is a host test rather than a claim.
fn latch_arrows_from(state: &[u8]) -> usize {
    let mut down = 0usize;
    if state[DIK_LEFT] & DIK_PRESSED != 0 {
        down |= SAVE_PICKER_NAV_LEFT_MASK;
    }
    if state[DIK_RIGHT] & DIK_PRESSED != 0 {
        down |= SAVE_PICKER_NAV_RIGHT_MASK;
    }
    if state[DIK_UP] & DIK_PRESSED != 0 {
        down |= SAVE_PICKER_NAV_UP_MASK;
    }
    if state[DIK_DOWN] & DIK_PRESSED != 0 {
        down |= SAVE_PICKER_NAV_DOWN_MASK;
    }
    let previous = DINPUT_DOWN_MASK.swap(down, Ordering::SeqCst);
    let rising = down & !previous;
    if rising != 0 {
        DINPUT_EDGE_LATCH.fetch_or(rising, Ordering::SeqCst);
    }
    rising
}

/// The keyboard detour, in the hook union's four-`usize` shape.
///
/// `DINPUT_KB_GET_STATE_ORIG` may hold the next handler in the chain rather than the game
/// trampoline, so it is called through [`UnionFn`] rather than the narrower three-argument
/// `GetDeviceState` signature. The `usize` return carries the `HRESULT` in its low 32 bits and is
/// passed straight back.
///
/// The buffer is read after the call, because before it the bytes are the previous frame's.
unsafe extern "system" fn dinput_kb_get_state_hook(
    device: usize,
    size: usize,
    data: usize,
    unused: usize,
) -> usize {
    DINPUT_KB_HOOK_FIRES.fetch_add(1, Ordering::Relaxed);
    let next = DINPUT_KB_GET_STATE_ORIG.load(Ordering::Relaxed);
    if next == 0 {
        return 0;
    }
    let call: UnionFn = unsafe { std::mem::transmute::<usize, UnionFn>(next) };
    let raw = unsafe { call(device, size, data, unused) };
    let (hr, size, data) = (raw as i32, size as usize, data as *const u8);
    if hr != 0 || data.is_null() || size <= DINPUT_KEYBOARD_BUFFER_FLOOR {
        return raw;
    }
    // Safety: DirectInput filled `size` bytes at `data` and reported success, and the slice is read
    // and dropped before returning.
    let state = unsafe { std::slice::from_raw_parts(data, size) };
    let rising = latch_arrows_from(state);
    if rising != 0 {
        let count = DINPUT_EDGE_COUNT.fetch_add(1, Ordering::SeqCst) + 1;
        if count <= 20 || count.is_multiple_of(50) {
            let down = DINPUT_DOWN_MASK.load(Ordering::SeqCst);
            append_autoload_debug(format_args!(
                "save-picker-nav: dinput arrow edge #{count} rising=0x{rising:x} down=0x{down:x}"
            ));
        }
    }
    raw
}

/// Resolve the keyboard's `GetDeviceState` by asking DirectInput for a throwaway device and reading
/// the slot out of its vtable. The device is released immediately; only the address is kept.
unsafe fn keyboard_get_device_state_address() -> Option<usize> {
    let dinput8 = unsafe { GetModuleHandleA(s!("dinput8.dll")) }.ok()?;
    let create = unsafe { GetProcAddress(dinput8, s!("DirectInput8Create")) }?;
    let di8_create: DInput8CreateFn = unsafe { std::mem::transmute(create) };
    let hinstance = unsafe { GetModuleHandleA(None) }
        .unwrap_or(HMODULE(std::ptr::null_mut()))
        .0 as usize;

    let mut di8: RawObj = std::ptr::null_mut();
    let hr = unsafe {
        di8_create(
            hinstance,
            DIRECTINPUT_VERSION,
            &IID_IDIRECTINPUT8W,
            &mut di8,
            0,
        )
    };
    if hr != 0 || di8.is_null() {
        append_autoload_debug(format_args!(
            "save-picker-nav: DirectInput8Create failed hr={hr:#010x}"
        ));
        return None;
    }
    let create_device: CreateDeviceFn = unsafe { vtable_fn(di8, VTBL_CREATE_DEVICE) };
    let release_di8: ReleaseFn = unsafe { vtable_fn(di8, VTBL_RELEASE) };
    let mut device: RawObj = std::ptr::null_mut();
    let hr = unsafe { create_device(di8, &GUID_SYS_KEYBOARD, &mut device, 0) };
    if hr != 0 || device.is_null() {
        unsafe { release_di8(di8) };
        append_autoload_debug(format_args!(
            "save-picker-nav: CreateDevice(SysKeyboard) failed hr={hr:#010x}"
        ));
        return None;
    }
    let address = unsafe { *(*device).add(VTBL_GET_DEVICE_STATE) as usize };
    let release_device: ReleaseFn = unsafe { vtable_fn(device, VTBL_RELEASE) };
    unsafe { release_device(device) };
    unsafe { release_di8(di8) };
    (address != 0).then_some(address)
}

/// Install the keyboard reader once. Cheap to call on every pump tick.
///
/// Through the union, never a bare `MhHook`: the product and two effects DLLs detour this same
/// `GetDeviceState` slot, each linking its own MinHook instance, and two instances on one prologue
/// overwrite each other's trampolines with the loser silently never running.
pub fn ensure_dinput_nav_reader() {
    if DINPUT_NAV_HOOK_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    let Some(address) = (unsafe { keyboard_get_device_state_address() }) else {
        // Left latched: dinput8 is not up yet or the device could not be made, and retrying every
        // tick would call into DirectInput four times a second for the life of the process.
        append_autoload_debug(format_args!(
            "save-picker-nav: no DirectInput keyboard; arrow navigation falls back to CS::MoveDir"
        ));
        return;
    };
    match unsafe {
        register_union_hook(address, dinput_kb_get_state_hook, &DINPUT_KB_GET_STATE_ORIG)
    } {
        Ok(()) => append_autoload_debug(format_args!(
            "save-picker-nav: hooked DirectInput keyboard GetDeviceState 0x{address:x}; arrows are read where the game reads them"
        )),
        Err(status) => {
            let status: MH_STATUS = status;
            append_autoload_debug(format_args!(
                "save-picker-nav: union register of GetDeviceState 0x{address:x} failed: {status:?}"
            ));
        }
    }
}

/// True once the detour is installed and has seen the game read the keyboard.
///
/// Both halves matter: installed alone would claim the reader before a single frame has proved the
/// game polls this device, and the fallback has to stay in charge until it does.
pub fn dinput_nav_reader_live() -> bool {
    DINPUT_NAV_HOOK_INSTALLED.load(Ordering::SeqCst) != 0
        && DINPUT_KB_HOOK_FIRES.load(Ordering::Relaxed) != 0
}

/// Directions held on the keyboard right now, consuming nothing.
pub fn dinput_nav_held() -> usize {
    DINPUT_DOWN_MASK.load(Ordering::SeqCst) & DINPUT_NAV_ALL_MASK
}

/// Drain only the requested directions, leaving the others latched for their own consumer.
///
/// Left/right (the drive strip) and up/down (the edge scroll) are drained by two different pumps in
/// one tick, so a consume-everything take would let whichever ran first swallow the other's edges.
pub fn dinput_take_nav_edges_for(mask: usize) -> usize {
    let mask = mask & DINPUT_NAV_ALL_MASK;
    DINPUT_EDGE_LATCH.fetch_and(!mask, Ordering::SeqCst) & mask
}

#[cfg(test)]
mod tests {
    use super::*;

    fn buffer_with(scancodes: &[usize]) -> Vec<u8> {
        let mut state = vec![0u8; 256];
        for &code in scancodes {
            state[code] = DIK_PRESSED;
        }
        state
    }

    fn reset() {
        DINPUT_DOWN_MASK.store(0, Ordering::SeqCst);
        DINPUT_EDGE_LATCH.store(0, Ordering::SeqCst);
    }

    #[test]
    fn a_held_arrow_reports_down_on_every_read_and_an_edge_only_on_the_first() {
        reset();
        let held = buffer_with(&[DIK_DOWN]);
        assert_eq!(latch_arrows_from(&held), SAVE_PICKER_NAV_DOWN_MASK);
        assert_eq!(dinput_nav_held(), SAVE_PICKER_NAV_DOWN_MASK);
        // The auto-repeat ticks: still down, no second edge. This is the whole difference from a
        // resolved-direction source, which reads as not-held between repeats.
        assert_eq!(latch_arrows_from(&held), 0);
        assert_eq!(latch_arrows_from(&held), 0);
        assert_eq!(dinput_nav_held(), SAVE_PICKER_NAV_DOWN_MASK);
        assert_eq!(
            dinput_take_nav_edges_for(SAVE_PICKER_NAV_DOWN_MASK),
            SAVE_PICKER_NAV_DOWN_MASK,
            "the one press is still owed a step"
        );
        assert_eq!(dinput_take_nav_edges_for(SAVE_PICKER_NAV_DOWN_MASK), 0);
    }

    #[test]
    fn releasing_and_pressing_again_is_a_second_edge() {
        reset();
        let held = buffer_with(&[DIK_UP]);
        let released = buffer_with(&[]);
        assert_eq!(latch_arrows_from(&held), SAVE_PICKER_NAV_UP_MASK);
        assert_eq!(latch_arrows_from(&released), 0);
        assert_eq!(dinput_nav_held(), 0);
        assert_eq!(latch_arrows_from(&held), SAVE_PICKER_NAV_UP_MASK);
    }

    #[test]
    fn a_drain_leaves_the_directions_it_was_not_asked_for() {
        reset();
        latch_arrows_from(&buffer_with(&[DIK_LEFT, DIK_DOWN]));
        assert_eq!(
            dinput_take_nav_edges_for(SAVE_PICKER_NAV_LEFT_MASK | SAVE_PICKER_NAV_RIGHT_MASK),
            SAVE_PICKER_NAV_LEFT_MASK
        );
        assert_eq!(
            dinput_take_nav_edges_for(SAVE_PICKER_NAV_UP_MASK | SAVE_PICKER_NAV_DOWN_MASK),
            SAVE_PICKER_NAV_DOWN_MASK,
            "the drive strip's drain must not swallow the scroll's edge"
        );
    }

    #[test]
    fn every_arrow_maps_to_its_own_direction() {
        reset();
        assert_eq!(
            latch_arrows_from(&buffer_with(&[DIK_RIGHT])),
            SAVE_PICKER_NAV_RIGHT_MASK
        );
        reset();
        assert_eq!(
            latch_arrows_from(&buffer_with(&[DIK_LEFT])),
            SAVE_PICKER_NAV_LEFT_MASK
        );
        reset();
        assert_eq!(
            latch_arrows_from(&buffer_with(&[DIK_UP])),
            SAVE_PICKER_NAV_UP_MASK
        );
        reset();
        assert_eq!(
            latch_arrows_from(&buffer_with(&[DIK_DOWN])),
            SAVE_PICKER_NAV_DOWN_MASK
        );
    }
}
