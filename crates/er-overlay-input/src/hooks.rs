//! The three hooks behind [`crate::set_taking`]: the cursor predicate, DirectInput, and XInput.

use std::sync::atomic::{AtomicBool, AtomicU16, AtomicU64, AtomicUsize, Ordering};

use er_game_base::game_build::{describe_build, is_supported_build};
use er_game_base::mem::{game_module_base, read_bytes};
use er_hook::UnionFn;
use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress};
use windows::core::s;

use crate::{InputCounters, LogFn, PadRead, pad_after, taking};

include!(concat!(env!("OUT_DIR"), "/generated_prologues.rs"));

/// The 2.7.2.0 rva of "a menu has the mouse", `bool(CSMenuManImp*)`. It is used on 2.7.2.0 only,
/// after its opening bytes are compared with `MENU_HAS_MOUSE_PROLOGUE`.
const MENU_HAS_MOUSE_RVA: usize = 0x0076_6b00;

// The cursor predicate.

static MENU_MOUSE_ORIG: AtomicUsize = AtomicUsize::new(0);
/// Predicate calls answered for the menu.
static CURSOR_FORCED: AtomicU64 = AtomicU64::new(0);
/// Set once the predicate is hooked.
static CURSOR_HOOKED: AtomicBool = AtomicBool::new(false);

/// The predicate, in the union's four-argument shape (it takes one).
unsafe extern "system" fn menu_has_mouse_union(
    menu_man: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    if taking() {
        CURSOR_FORCED.fetch_add(1, Ordering::Relaxed);
        return 1;
    }
    let next = MENU_MOUSE_ORIG.load(Ordering::Acquire);
    if next == 0 {
        return 0;
    }
    // SAFETY: the next handler or the trampoline, both `UnionFn`.
    let call: UnionFn = unsafe { core::mem::transmute::<usize, UnionFn>(next) };
    unsafe { call(menu_man, b, c, d) }
}

/// Hook the predicate on 2.7.2.0. Any other build, or other bytes at the address, leaves the cursor
/// to the game and says so: the menu still opens and still takes the buttons, but the camera turns
/// with the mouse and the cursor stays hidden. Returns whether the predicate is hooked.
pub fn install_cursor(log: LogFn) -> bool {
    if CURSOR_HOOKED.load(Ordering::Acquire) {
        return true;
    }
    // Measured on 2.7.2.0. The opening bytes are compared below before anything is hooked.
    let Some(rva) = is_supported_build().then_some(MENU_HAS_MOUSE_RVA) else {
        log(format_args!(
            "cursor: the menu-mouse predicate was measured on 2.7.2.0 and this build is another \
             ({}); the menu gets no free cursor",
            describe_build()
        ));
        return false;
    };
    let Ok(base) = game_module_base() else {
        return false;
    };
    let entry = base + rva;
    let mut bytes = [0u8; MENU_HAS_MOUSE_PROLOGUE.len()];
    // SAFETY: a fault-tolerant read of code bytes.
    let read = unsafe { read_bytes(entry, &mut bytes) };
    // Byte 0 may be a `jmp rel32` another module's detour wrote, which the hub follows; the rest
    // must match.
    let matches = read
        && (bytes == MENU_HAS_MOUSE_PROLOGUE
            || (bytes[0] == 0xe9 && bytes[5..] == MENU_HAS_MOUSE_PROLOGUE[5..]));
    if !matches {
        log(format_args!(
            "cursor: 0x{entry:x} does not open like the menu-mouse predicate ({bytes:02x?}); not \
             hooked"
        ));
        return false;
    }
    // Through the elected hub, not this DLL's own union. Every menu DLL answers this predicate, and
    // each registering on its own union put one MinHook instance per DLL on the entry: on
    // 2026-10-22 three of four read the pristine prologue before any had written its jump, and only
    // the last to enable was ever called. One hub, one detour, every handler in its chain.
    // SAFETY: a runtime-derived 2.7.2.0 entry whose bytes were just checked; the handler calls its
    // slot through `UnionFn`.
    match unsafe {
        er_hook::register_shared_hook_following_arxan(entry, menu_has_mouse_union, &MENU_MOUSE_ORIG)
    } {
        Ok(route) => {
            CURSOR_HOOKED.store(true, Ordering::Release);
            log(format_args!(
                "cursor: menu-mouse predicate at 0x{entry:x} answered while the menu is open \
                 ({route:?})"
            ));
            true
        }
        Err(status) => {
            log(format_args!(
                "cursor: menu-mouse predicate at 0x{entry:x} refused: {status:?}"
            ));
            false
        }
    }
}

// DirectInput.

static MOUSE_ARMED: AtomicBool = AtomicBool::new(false);
static KEYBOARD_ARMED: AtomicBool = AtomicBool::new(false);

/// Arm the keyboard and mouse blanking once `dinput8.dll` is loaded. Returns true once both are;
/// cheap to call every frame until then.
pub fn try_install_dinput(log: LogFn) -> bool {
    if !MOUSE_ARMED.load(Ordering::Relaxed) {
        // SAFETY: called off the loader lock, after the game has created its devices.
        if let Ok(addr) = unsafe { er_dinput_suppress_core::install_mouse_suppression() } {
            MOUSE_ARMED.store(true, Ordering::Relaxed);
            log(format_args!("input: mouse blanking armed at 0x{addr:x}"));
        }
    }
    if !KEYBOARD_ARMED.load(Ordering::Relaxed) {
        // SAFETY: as above.
        if let Ok(addr) = unsafe { er_dinput_suppress_core::install_keyboard_suppression() } {
            KEYBOARD_ARMED.store(true, Ordering::Relaxed);
            log(format_args!("input: keyboard blanking armed at 0x{addr:x}"));
        }
    }
    MOUSE_ARMED.load(Ordering::Relaxed) && KEYBOARD_ARMED.load(Ordering::Relaxed)
}

// XInput.

/// `XINPUT_STATE`: `u32 dwPacketNumber`, then the 12-byte `XINPUT_GAMEPAD`.
const GAMEPAD_OFFSET: usize = 4;
const GAMEPAD_BYTES: usize = 12;
/// What `XInputGetState` answers for a pad that is not there.
const ERROR_DEVICE_NOT_CONNECTED: usize = 0x48f;

static PAD_ORIG: AtomicUsize = AtomicUsize::new(0);
static PAD_INSTALLED: AtomicBool = AtomicBool::new(false);
/// Buttons that were down when taken and have not been seen released since.
static PAD_HELD: AtomicU16 = AtomicU16::new(0);
/// Pad reads this module zeroed.
static PAD_ZEROED: AtomicU64 = AtomicU64::new(0);

/// `XInputGetState(DWORD, XINPUT_STATE*) -> DWORD` in the union's four-argument shape.
unsafe extern "system" fn xinput_get_state(
    user_index: usize,
    state: usize,
    c: usize,
    d: usize,
) -> usize {
    let next = PAD_ORIG.load(Ordering::Acquire);
    if next == 0 {
        return ERROR_DEVICE_NOT_CONNECTED;
    }
    // SAFETY: the next handler or the trampoline, both `UnionFn`.
    let call: UnionFn = unsafe { core::mem::transmute::<usize, UnionFn>(next) };
    let result = unsafe { call(user_index, state, c, d) };
    if result as u32 != 0 || state == 0 || user_index as u32 != 0 {
        return result;
    }
    let gamepad = (state + GAMEPAD_OFFSET) as *mut u8;
    let buttons_at = gamepad.cast::<u16>();
    // SAFETY: the 16-byte structure the caller passed and the chain just filled.
    let buttons = unsafe { buttons_at.read_unaligned() };
    match pad_after(taking(), buttons, PAD_HELD.load(Ordering::Acquire)) {
        PadRead::Zeroed { held } => {
            PAD_HELD.store(held, Ordering::Release);
            // SAFETY: as above.
            unsafe { std::ptr::write_bytes(gamepad, 0, GAMEPAD_BYTES) };
            PAD_ZEROED.fetch_add(1, Ordering::Relaxed);
        }
        PadRead::Passed {
            buttons: passed,
            held,
        } => {
            PAD_HELD.store(held, Ordering::Release);
            if passed != buttons {
                // SAFETY: as above.
                unsafe { buttons_at.write_unaligned(passed) };
            }
        }
    }
    result
}

/// Hook `XInputGetState` in whichever XInput module the game loaded. Returns true once hooked.
///
/// The export on this Wine prefix is a five-byte `jmp rel32` forwarding thunk, and detouring the
/// thunk reports success while nothing runs (measured by `er-quickload`'s input blocker), so the
/// jump is followed first, as `er-npc-summons/src/pad.rs` does, and both register on the function
/// it reaches through the shared union.
pub fn try_install_pad(log: LogFn) -> bool {
    if PAD_INSTALLED.load(Ordering::Relaxed) {
        return true;
    }
    for name in [
        s!("xinput1_4.dll"),
        s!("xinput1_3.dll"),
        s!("xinput9_1_0.dll"),
    ] {
        // SAFETY: a plain module lookup by name.
        let Ok(module) = (unsafe { GetModuleHandleA(name) }) else {
            continue;
        };
        // SAFETY: an export lookup in a loaded module.
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
        PAD_INSTALLED.store(true, Ordering::Relaxed);
        // SAFETY: the handler has the union's shape and calls its slot through `UnionFn`.
        let route = unsafe {
            er_hook::register_shared_hook_with_budget(target, xinput_get_state, &PAD_ORIG, 1, 0)
        };
        log(format_args!(
            "input: XInputGetState at 0x{target:x} (export 0x{:x}) {route:?}",
            exported as usize
        ));
        return true;
    }
    false
}

/// The hooks' half of [`crate::counters`].
pub(crate) fn counters() -> InputCounters {
    InputCounters {
        cursor_hooked: CURSOR_HOOKED.load(Ordering::Relaxed),
        cursor_forced: CURSOR_FORCED.load(Ordering::Relaxed),
        keyboard_armed: KEYBOARD_ARMED.load(Ordering::Relaxed),
        mouse_armed: MOUSE_ARMED.load(Ordering::Relaxed),
        pad_hooked: PAD_INSTALLED.load(Ordering::Relaxed),
        pad_zeroed: PAD_ZEROED.load(Ordering::Relaxed),
        ..InputCounters::default()
    }
}
