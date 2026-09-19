//! Resolving the mouse's `GetDeviceState` and chaining one handler onto it.
//!
//! Everything here is the install; the blanking itself is in the crate root and is callable
//! without it, because a module that already owns a detour on the same entry should run the
//! blanking rather than register a second handler.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_hook::{MH_STATUS, UnionFn, register_shared_hook_with_budget};
use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress};
use windows::core::{GUID, s};

/// A single non-blocking probe for the union export. The install is driven from a game frame, and
/// by the time one runs every native in the profile is loaded, so there is nothing left to poll
/// for -- and polling on the game thread would be a visible stall.
const FRAME_DRIVEN_RESOLVE_TRIES: u32 = 1;
const FRAME_DRIVEN_RESOLVE_SLEEP_MS: u32 = 0;

const DIRECTINPUT_VERSION: u32 = 0x0800;

const IID_IDIRECTINPUT8W: GUID = GUID::from_values(
    0xbf798031,
    0x483a,
    0x4da2,
    [0xaa, 0x99, 0x5d, 0x64, 0xed, 0x36, 0x97, 0x00],
);
const GUID_SYS_MOUSE: GUID = GUID::from_values(
    0x6F1D2B60,
    0xD5A0,
    0x11CF,
    [0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00],
);

const VTBL_RELEASE: usize = 2;
const VTBL_CREATE_DEVICE: usize = 3;
const VTBL_GET_DEVICE_STATE: usize = 9;

type RawObj = *mut *const usize;
type DInput8CreateFn =
    unsafe extern "system" fn(usize, u32, *const GUID, *mut RawObj, usize) -> i32;
type CreateDeviceFn = unsafe extern "system" fn(RawObj, *const GUID, *mut RawObj, usize) -> i32;
type ReleaseFn = unsafe extern "system" fn(RawObj) -> u32;

static INSTALLED: AtomicBool = AtomicBool::new(false);
static ORIG: AtomicUsize = AtomicUsize::new(0);
static HOOK_FIRES: AtomicUsize = AtomicUsize::new(0);

/// Install the mouse-click suppression detour, idempotently.
///
/// Drive it from a game-frame tick and keep calling until it reports `Ok`: `dinput8.dll` may not
/// be loaded when a DLL attaches, and MinHook must not run under the loader lock.
///
/// The detour is registered through `er-hook`'s union, never as a bare `MhHook`. `er-net-effects`
/// (its selector), `er-quickload` (its input blocker) and `er-enemynpc-effects` (its hotkey) all
/// detour a `GetDeviceState` entry, and two separately linked MinHook instances on one prologue
/// overwrite each other's trampolines -- the loser reports installed and never runs. The union
/// chains them instead. See the `[[shared]]` row in `scripts/me3-dll-conflicts.toml`.
///
/// # Safety
///
/// Call from a game thread, after the process has run at least one frame.
pub unsafe fn install_mouse_suppression() -> Result<usize, MH_STATUS> {
    if INSTALLED.load(Ordering::Relaxed) {
        return Ok(0);
    }

    let dinput8 = unsafe { GetModuleHandleA(s!("dinput8.dll")) }
        .map_err(|_| MH_STATUS::MH_ERROR_MODULE_NOT_FOUND)?;
    let di8_create: DInput8CreateFn = unsafe {
        std::mem::transmute(
            GetProcAddress(dinput8, s!("DirectInput8Create"))
                .ok_or(MH_STATUS::MH_ERROR_FUNCTION_NOT_FOUND)?,
        )
    };
    let hinstance = unsafe { GetModuleHandleA(None) }
        .map_err(|_| MH_STATUS::MH_ERROR_MODULE_NOT_FOUND)?
        .0 as usize;

    let mut mouse_addr = 0usize;
    unsafe {
        with_probe_device(di8_create, hinstance, &GUID_SYS_MOUSE, |addr| {
            mouse_addr = addr;
        })?;
    }

    unsafe {
        register_shared_hook_with_budget(
            mouse_addr,
            mouse_get_state_hook,
            &ORIG,
            FRAME_DRIVEN_RESOLVE_TRIES,
            FRAME_DRIVEN_RESOLVE_SLEEP_MS,
        )?;
    }

    publish_detour_addresses(mouse_addr);
    // The registrar owns the detour for the life of the process; nothing uninstalls it.
    INSTALLED.store(true, Ordering::Relaxed);
    Ok(mouse_addr)
}

/// How many times the installed detour has run. Zero after a successful install is the signature
/// of a detour that lost its prologue to another MinHook instance.
#[must_use]
pub fn mouse_hook_fires() -> usize {
    HOOK_FIRES.load(Ordering::Relaxed)
}

/// Record which detours resolved, for the product's runtime oracles.
fn publish_detour_addresses(mouse_addr: usize) {
    er_telemetry_core::counters::DINPUT_MOUSE_GET_STATE_ORIG.store(mouse_addr, Ordering::Relaxed);
}

/// How many clicks this module has kept out of the game.
#[must_use]
pub fn suppressed_mouse_clicks() -> usize {
    crate::suppressed_clicks()
}

/// The detour, in the hook union's four-`usize` shape.
///
/// `ORIG` may hold the next handler in the chain rather than the game trampoline, so it is called
/// through [`UnionFn`] and not through the narrower three-argument `GetDeviceState` signature.
/// The `usize` return carries the `HRESULT` in its low 32 bits, which is where the caller reads it
/// from, so it is passed straight back.
unsafe extern "system" fn mouse_get_state_hook(
    device: usize,
    size: usize,
    data: usize,
    unused: usize,
) -> usize {
    HOOK_FIRES.fetch_add(1, Ordering::Relaxed);
    // The same count, in the shared table the product's runtime oracles read. This module owns the
    // detour now, so it owns the counter's only write site: `er-net-effects` used to hold both and
    // moving one without the other leaves the oracle reading 0 forever, which reads as "the mouse
    // was never polled" rather than "nobody wrote this".
    er_telemetry_core::counters::DINPUT_MOUSE_HOOK_FIRES.fetch_add(1, Ordering::Relaxed);
    let next = ORIG.load(Ordering::Relaxed);
    if next == 0 {
        return 0;
    }
    let call: UnionFn = unsafe { std::mem::transmute::<usize, UnionFn>(next) };
    let raw = unsafe { call(device, size, data, unused) };
    // Shape-checked inside: the mouse and keyboard can share one vtable entry, so a 256-byte DIK
    // table can arrive here and must be left alone.
    unsafe { crate::blank_overlay_mouse_click(raw as i32, size as u32, data as *mut u8) };
    raw
}

unsafe fn vtable_fn<F: Copy>(obj: RawObj, slot: usize) -> F {
    unsafe { std::mem::transmute_copy(&*(*obj).add(slot)) }
}

unsafe fn with_probe_device(
    di8_create: DInput8CreateFn,
    hinstance: usize,
    guid: &GUID,
    f: impl FnOnce(usize),
) -> Result<(), MH_STATUS> {
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
        return Err(MH_STATUS::MH_ERROR_FUNCTION_NOT_FOUND);
    }

    let create_device: CreateDeviceFn = unsafe { vtable_fn(di8, VTBL_CREATE_DEVICE) };
    let mut device: RawObj = std::ptr::null_mut();
    let hr = unsafe { create_device(di8, guid, &mut device, 0) };
    if hr != 0 || device.is_null() {
        let release_di8: ReleaseFn = unsafe { vtable_fn(di8, VTBL_RELEASE) };
        unsafe { release_di8(di8) };
        return Err(MH_STATUS::MH_ERROR_FUNCTION_NOT_FOUND);
    }

    let get_state_addr = unsafe { *(*device).add(VTBL_GET_DEVICE_STATE) as usize };
    f(get_state_addr);

    let release_device: ReleaseFn = unsafe { vtable_fn(device, VTBL_RELEASE) };
    let release_di8: ReleaseFn = unsafe { vtable_fn(di8, VTBL_RELEASE) };
    unsafe { release_device(device) };
    unsafe { release_di8(di8) };
    Ok(())
}
