//! The pad buttons the picker navigates with, taken from the game while it is open.
//!
//! One handler on `XInputGetState`, registered through `er-hook`'s shared union. The export on
//! this Wine prefix is a five-byte `jmp rel32` forwarding thunk, not a function: detouring it
//! writes over the jump, and the install reports success while nothing runs (measured by
//! `er-quickload`'s `input_blocker` on run br-20260916-081922-9b62). So the thunk is followed
//! first, exactly as that install does, and the union is registered on the function it jumps
//! to; `er-quickload` registers its own pad handler on the same address through its union, so
//! the two chain (the `[[shared]]` row in `scripts/me3-dll-conflicts.toml`).
//!
//! The handler calls the chain, keeps the raw `wButtons` of pad 0 for the picker (which can no
//! longer call `XInputGetState` itself and see the truth, since the buttons it reads are the ones
//! cleared here), and while the picker is open clears d-pad up, d-pad down, A and B. A button
//! taken while down stays cleared until it is released, so the A that picks a row does not also
//! reach the game the frame the picker closes.

#![cfg(windows)]

use std::sync::atomic::{AtomicBool, AtomicU16, AtomicUsize, Ordering};

use er_hook::UnionFn;
use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress};
use windows::core::s;

use crate::log::summons_log;

/// `XINPUT_STATE`: `u32 dwPacketNumber`, then `XINPUT_GAMEPAD` whose first field is `wButtons`.
const STATE_BUTTONS_OFFSET: usize = 4;
const DPAD_UP: u16 = 0x0001;
const DPAD_DOWN: u16 = 0x0002;
const BUTTON_A: u16 = 0x1000;
const BUTTON_B: u16 = 0x2000;
/// The buttons taken while the picker is open.
const TAKEN_BUTTONS: u16 = DPAD_UP | DPAD_DOWN | BUTTON_A | BUTTON_B;

static ORIG: AtomicUsize = AtomicUsize::new(0);
static INSTALLED: AtomicBool = AtomicBool::new(false);
static TAKING: AtomicBool = AtomicBool::new(false);
/// Taken buttons not yet seen released.
static HELD: AtomicU16 = AtomicU16::new(0);
/// Pad 0's buttons as the chain returned them, before anything was cleared.
static RAW_BUTTONS: AtomicU16 = AtomicU16::new(0);
/// Successful pad-0 reads seen; the picker trusts [`RAW_BUTTONS`] only once this moves.
static READS: AtomicUsize = AtomicUsize::new(0);
static SUPPRESSED: AtomicUsize = AtomicUsize::new(0);

/// Take the picker's buttons from the game, or give them back.
pub(crate) fn set_taking(taking: bool) {
    TAKING.store(taking, Ordering::Release);
}

/// Pad 0's raw buttons from the game's last read, or `None` before the hook has seen one.
pub(crate) fn raw_buttons() -> Option<u16> {
    (READS.load(Ordering::Acquire) != 0).then(|| RAW_BUTTONS.load(Ordering::Acquire))
}

/// Button reads cleared so far.
pub(crate) fn suppressed() -> usize {
    SUPPRESSED.load(Ordering::Relaxed)
}

/// `XInputGetState(DWORD, XINPUT_STATE*) -> DWORD` in the union's four-argument shape.
unsafe extern "system" fn picker_xinput_get_state(
    user_index: usize,
    state: usize,
    c: usize,
    d: usize,
) -> usize {
    let next = ORIG.load(Ordering::Acquire);
    if next == 0 {
        return 0x48f; // ERROR_DEVICE_NOT_CONNECTED
    }
    // SAFETY: the next handler or the trampoline, both `UnionFn`.
    let call: UnionFn = unsafe { core::mem::transmute::<usize, UnionFn>(next) };
    let result = unsafe { call(user_index, state, c, d) };
    if result as u32 != 0 || state == 0 || user_index as u32 != 0 {
        return result;
    }
    let buttons_at = (state + STATE_BUTTONS_OFFSET) as *mut u16;
    // SAFETY: the 16-byte structure the caller passed and the chain just filled.
    let raw = unsafe { buttons_at.read_unaligned() };
    RAW_BUTTONS.store(raw, Ordering::Release);
    READS.fetch_add(1, Ordering::AcqRel);
    let taken = if TAKING.load(Ordering::Acquire) {
        TAKEN_BUTTONS
    } else {
        0
    };
    let cleared = raw & (taken | HELD.load(Ordering::Acquire));
    HELD.store(cleared, Ordering::Release);
    if cleared != 0 {
        // SAFETY: as above.
        unsafe { buttons_at.write_unaligned(raw & !cleared) };
        SUPPRESSED.fetch_add(1, Ordering::Relaxed);
    }
    result
}

/// Hook `XInputGetState` in whichever XInput module the game loaded; retried from the game task
/// until it succeeds, because the module arrives after the DLL attaches.
pub(crate) fn try_install() {
    if INSTALLED.load(Ordering::Relaxed) {
        return;
    }
    for name in [
        s!("xinput1_4.dll"),
        s!("xinput1_3.dll"),
        s!("xinput9_1_0.dll"),
    ] {
        let Ok(module) = (unsafe { GetModuleHandleA(name) }) else {
            continue;
        };
        let Some(exported) = (unsafe { GetProcAddress(module, s!("XInputGetState")) }) else {
            continue;
        };
        const JMP_REL32: u8 = 0xe9;
        const JMP_REL32_LEN: usize = 5;
        let mut target = exported as usize;
        // SAFETY: the first bytes of a resolved export in a loaded module.
        if unsafe { *(target as *const u8) } == JMP_REL32 {
            let displacement = unsafe { ((target + 1) as *const i32).read_unaligned() };
            target = (target + JMP_REL32_LEN).wrapping_add_signed(displacement as isize);
        }
        INSTALLED.store(true, Ordering::Relaxed);
        // SAFETY: the handler has the union's shape and calls its slot through `UnionFn`.
        let route = unsafe {
            er_hook::register_shared_hook_with_budget(target, picker_xinput_get_state, &ORIG, 1, 0)
        };
        summons_log(format_args!(
            "pad: XInputGetState at 0x{:x} (export 0x{:x}) {route:?}",
            target, exported as usize
        ));
        return;
    }
}
