//! Can-move readiness probe (2026-07-18, user-directed): PROVE that input actually moves the
//! character, not just that it is render-ready. "render-ready" says the character can be SEEN;
//! this says input MOVES it. `play_time` advancing is necessary but not sufficient (it ticks during
//! the freeze), so movement is proven by a havok-POSITION delta under a KNOWN injected forward stick,
//! sustained for `MOVE_PROBE_REQUIRED_FRAMES` (60) consecutive frames per load -- a real walk, not a
//! one-frame twitch. Runs on the game thread (safe to drive input); the XInput hook stamps the stick
//! when `MOVE_PROBE_ACTIVE`.
//!
//! Per load epoch (fresh_deser_count) the probe resets, then each render-ready frame it injects the
//! forward stick and counts consecutive frames whose horizontal displacement clears the threshold.
//! A static/frozen character repeats its position exactly (delta ~0), so it never accumulates; a
//! walking character clears 60 frames quickly and latches `CAN_MOVE_CONFIRMED`.

use std::ffi::c_void;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::constants::{
    CAN_MOVE_CONFIRMED, DID_MOVE_FRAMES, HARNESS_MOVE_VERDICT, IN_GAME_STEP_REQUEST_CODE_D8_OFFSET,
    INGAMESTEP_MOVEMAPSTEP_PTR_OFFSET, INGAMESTEP_REQUEST_CODE_MOVEMAP_PENDING,
    INGAMESTEP_REQUEST_CODE_STABLE_IN_WORLD, MOVE_PROBE_ACTIVE, MOVE_PROBE_EPOCH,
    MOVE_PROBE_MOVED_FRAMES, MOVE_PROBE_PER_FRAME_THRESHOLD, MOVEMAPSTEP_CONTROL_ENABLE_4BA_OFFSET,
    MOVEMAPSTEP_COUNTDOWN_100_OFFSET, MOVEMAPSTEP_FINALIZE_SUBSTATE_12A_OFFSET,
    MOVEMAPSTEP_RESIDENT_UPDATE_STATE, MOVEMAPSTEP_STATE_48_RE_OFFSET,
    MOVEMAPSTEP_TASK_REGISTRATION_4B8_OFFSET, ORACLE_RELIABLE_INGAME_PTR,
    SUPPLIED_MOVEMENT_INPUT_FRAMES, SYSTEM_QUIT_CONTINUE_CONFIRM_FRESH_DESER_COUNT,
};

/// DLUID (input-device manager) singleton RVA + its input-accept-while-unfocused flag offset. Holding
/// `[DLUID+0x88d]=1` every probe frame makes ER apply the injected pad stick even while the window is
/// UNFOCUSED (bd breakthrough-pad-boundary-injection-moves-char-needs-focus). Tied DIRECTLY to the
/// probe here -- NOT the `er-quickload-stay-active.txt` marker, which the samechar-3x run script sweeps,
/// so the injected stick was being discarded while ER was unfocused (bd
/// canmove-contaminated-user-moved-harness-never-supplied). Fault-safe (null/low-ptr guarded).
const DLUID_SINGLETON_RVA: u32 = 0x485dc18;
const DLUID_INPUT_ACTIVE_FLAG_OFFSET: usize = 0x88d;
const HEAP_LO: usize = 0x1_0000;

fn movement_input_ready() -> bool {
    let ingame = ORACLE_RELIABLE_INGAME_PTR.load(Ordering::SeqCst);
    if ingame < HEAP_LO {
        return false;
    }
    let request_code =
        unsafe { crate::experiments::safe_read_i32(ingame + IN_GAME_STEP_REQUEST_CODE_D8_OFFSET) };
    let move_map =
        unsafe { crate::experiments::safe_read_usize(ingame + INGAMESTEP_MOVEMAPSTEP_PTR_OFFSET) };

    // Keep the ordinary teardown-complete path, but do not require it: 1.16.2 intentionally remains
    // in requestCode=1 / STEP_MoveMap=18 while the resident world is playable. requestCode=2 and a
    // null MoveMap child belong to ending/Cleanup/Finish teardown, not normal movement readiness.
    if request_code == Some(INGAMESTEP_REQUEST_CODE_STABLE_IN_WORLD) && move_map == Some(0) {
        return true;
    }
    let Some(mms) = move_map.filter(|m| *m >= HEAP_LO) else {
        return false;
    };
    request_code == Some(INGAMESTEP_REQUEST_CODE_MOVEMAP_PENDING)
        && unsafe {
            crate::experiments::safe_read_i32(mms + MOVEMAPSTEP_STATE_48_RE_OFFSET)
                == Some(MOVEMAPSTEP_RESIDENT_UPDATE_STATE)
                && crate::experiments::safe_read_u8(mms + MOVEMAPSTEP_FINALIZE_SUBSTATE_12A_OFFSET)
                    == Some(0)
                && crate::experiments::safe_read_i32(mms + MOVEMAPSTEP_COUNTDOWN_100_OFFSET)
                    == Some(0)
                && crate::experiments::safe_read_u8(mms + MOVEMAPSTEP_TASK_REGISTRATION_4B8_OFFSET)
                    == Some(1)
                && crate::experiments::safe_read_u8(mms + MOVEMAPSTEP_CONTROL_ENABLE_4BA_OFFSET)
                    == Some(1)
        }
}

fn hold_input_active() {
    let Ok(slot) = crate::game_rva(DLUID_SINGLETON_RVA) else {
        return;
    };
    // The singleton SLOT is module memory (always mapped); read the DLUID heap pointer from it.
    let dluid = unsafe { std::ptr::read_volatile(slot as *const usize) };
    if dluid < HEAP_LO {
        return; // singleton not yet constructed
    }
    // SAFETY: dluid is a live heap object once non-null; +0x88d is a byte the game itself writes.
    unsafe { std::ptr::write_volatile((dluid + DLUID_INPUT_ACTIVE_FLAG_OFFSET) as *mut u8, 1u8) };
}
use crate::mh::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};
use crate::telemetry::append_autoload_debug;

/// FD4PadDevice poll (deobf `0x141f6bad0`, RE `er-movement-input-stick-boundary-2026-07-18`): the
/// per-device, per-frame function where XInput / DirectInput / ScePad all deposit the device's
/// normalized analog-stick into `this`, BELOW the OS/Steam-Input layer and BEFORE locomotion reads it.
/// Our synthetic `XInputGetState(0)` never moved the character because Steam Input routes the pad
/// through ScePad/DirectInput, not the raw xinput DLL. Hooking here and writing the left-stick injects a
/// controller stick deflection at the game's OWN input boundary -- run through the full deadzone ->
/// mapping -> locomotion chain, identical to any real pad, robust to Steam Input. This injects INPUT
/// (a stick push), NOT the locomotion output, so it faithfully tests "does input move the character".
const FD4_PAD_DEVICE_POLL_RVA: u32 = 0x1f6bad0;
const PAD_STICK_LX_OFFSET: usize = 0x89c; // f32 in [-1.0, 1.0]
const PAD_STICK_LY_OFFSET: usize = 0x8a0; // f32 in [-1.0, 1.0]; +1.0 = full forward
pub(crate) use er_telemetry_core::counters::ORIG_PAD_POLL;

/// The vtable of the object the game's OWN poll wrote the stick into, latched from the hook's
/// `this`.
///
/// This is the sweep's proof of class, and it costs nothing to obtain because the game hands it
/// over every frame. The hooked function is a vtable slot of `DLUID::PadDevice`
/// (`0x1430c9f08` -> `0x1430cd048`, its only vtable reference in either image) and writes
/// `+0x89c`/`+0x8a0` on its own `this`, so whatever object reaches this hook is by construction the
/// class those two floats belong to and is `HeapAlloc(0xa68)` = 2664 bytes -- room to spare for a
/// write ending at `0x8a4`. `inject_all_pad_devices` then writes ONLY into objects carrying this
/// same vtable, which is what makes its writes provably in-bounds without resolving a data address
/// the 1.17 map does not carry.
static POLLED_DEVICE_VTABLE: AtomicUsize = AtomicUsize::new(0);

unsafe extern "system" fn pad_poll_hook(this: usize, a: usize, b: usize, c: usize) -> usize {
    let orig = ORIG_PAD_POLL.load(Ordering::SeqCst);
    let ret = if orig != 0 {
        let f: unsafe extern "system" fn(usize, usize, usize, usize) -> usize =
            unsafe { std::mem::transmute(orig) };
        unsafe { f(this, a, b, c) }
    } else {
        0
    };
    // After the poll filled the stick from the real source, overwrite with FULL FORWARD while probing.
    // Every device is overwritten; the priority moderator's active device is the one that moves the char.
    if this != 0 && MOVE_PROBE_ACTIVE.load(Ordering::SeqCst) {
        if let Some(vtable) = unsafe { crate::experiments::safe_read_usize(this) } {
            POLLED_DEVICE_VTABLE.store(vtable, Ordering::SeqCst);
        }
        unsafe {
            *((this + PAD_STICK_LX_OFFSET) as *mut f32) = 0.0;
            *((this + PAD_STICK_LY_OFFSET) as *mut f32) = 1.0;
        }
        // SUPPLIED_MOVEMENT_INPUT: we actually wrote the forward stick into a live pad device this
        // frame (distinct from whether it MOVED the character -- see DID_MOVE).
        SUPPLIED_MOVEMENT_INPUT_FRAMES.fetch_add(1, Ordering::Relaxed);
    }
    ret
}

/// FD4PadManager singleton RVA (GLOBAL_FD4PadManager, dump 0x14485dc20 == DLUID+0x8). Its `padDevices`
/// is a `DLFixedVector<FD4PadDevice*,4>`: inline entries at +0x18, count at +0x40.
/// bd er-movement-input-stick-boundary-2026-07-18.
const FD4_PAD_MANAGER_RVA: u32 = 0x485dc20;
const PAD_MGR_DEVICES_OFFSET: usize = 0x18;
const PAD_MGR_DEVICE_COUNT_OFFSET: usize = 0x40;

/// `FD4PadDevice`'s OWN `DLFixedVector<DLUID::device*,4>`: entries at +0x10, count at +0x38.
///
/// CORRECTED 2026-08-31, and it was a heap overrun. This used to read `FD4PadDevice + 0x8` and call
/// the result "the concrete device". `FD4::FD4PadDevice::FD4PadDevice` (1.16.2 `0x142663880`) does
/// set `+0x8`, but from `DLUserInputManagerImpl`'s device factory with type **7**, and that factory
/// (`0x141f28a80` -> `0x141f2a880`) answers type 7 with `HeapAlloc(0x7f8)` +
/// `DLUID::VirtualMultiDevice::VirtualMultiDevice` -- the aggregator, 2040 bytes. Writing a float at
/// `+0x8a0` puts bytes 2208..2211 into it, so BOTH stores landed entirely past the end of a live
/// allocation, up to 172 bytes out. (The type-7 path is unconditional: the GUID lookup
/// `0x141f286c0` returns its null sentinel for anything outside 1, 2 and 3..6, so the DirectInput
/// branch that could have produced a larger object is never taken for 7.)
///
/// The real per-pad devices are the fixed vector the same constructor fills from types 3..6, each a
/// `DLUID::PadDevice` = `HeapAlloc(0xa68)` = 2664 bytes, which is the class that owns
/// `+0x89c`/`+0x8a0` -- the game's own poll writes those two floats on its `this`. Element `i` is at
/// `+0x10 + i*8` and the count is at `+0x38`, bounded by the constructor's own
/// `if (4 < count + 1) DLPanic("out of memory")`.
const FD4PADDEVICE_DEVICES_OFFSET: usize = 0x10;
const FD4PADDEVICE_DEVICE_COUNT_OFFSET: usize = 0x38;
const FD4PADDEVICE_DEVICES_CAPACITY: usize = 4;

/// Write full-forward LY (neutral LX) into every registered pad device that is the SAME CLASS the
/// game's own poll just wrote the stick into, not just the one the poll hook fired for this frame.
///
/// The class test is the point. `+0x89c`/`+0x8a0` are fields of `DLUID::PadDevice` (0xa68 bytes);
/// the same factory also hands out `KeyboardDevice` (0x8f0), a 0x810 device and the 0x7f8
/// `VirtualMultiDevice`, and a write ending at `0x8a4` fits in only two of those four. Rather than
/// resolve a vtable address the 1.17 data map does not carry, the sweep compares against
/// [`POLLED_DEVICE_VTABLE`] -- the vtable of the object the engine itself polled and wrote these
/// exact fields on. Anything that does not match is skipped, so a write can never land in a device
/// class these offsets do not belong to. Every deref is low-pointer guarded. Called only while
/// injecting.
unsafe fn inject_all_pad_devices() {
    // No engine-polled device seen yet -> nothing to compare a class against, so write nothing.
    // A sweep with no class evidence is exactly what put 172 bytes past the end of a 0x7f8 object.
    let want_vtable = POLLED_DEVICE_VTABLE.load(Ordering::SeqCst);
    if want_vtable == 0 {
        return;
    }
    let Ok(mgr_ptr) = crate::game_rva(FD4_PAD_MANAGER_RVA) else {
        return;
    };
    let mgr = unsafe { *(mgr_ptr as *const usize) };
    if mgr < 0x10000 {
        return;
    }
    let count = (unsafe { *((mgr + PAD_MGR_DEVICE_COUNT_OFFSET) as *const u32) } as usize).min(4);
    for i in 0..count {
        let pad = unsafe { *((mgr + PAD_MGR_DEVICES_OFFSET + i * 8) as *const usize) };
        if pad < 0x10000 {
            continue;
        }
        let devices = (unsafe { *((pad + FD4PADDEVICE_DEVICE_COUNT_OFFSET) as *const u32) }
            as usize)
            .min(FD4PADDEVICE_DEVICES_CAPACITY);
        for slot in 0..devices {
            let device =
                unsafe { *((pad + FD4PADDEVICE_DEVICES_OFFSET + slot * 8) as *const usize) };
            if device < 0x10000 {
                continue;
            }
            if unsafe { *(device as *const usize) } != want_vtable {
                continue;
            }
            unsafe {
                *((device + PAD_STICK_LX_OFFSET) as *mut f32) = 0.0;
                *((device + PAD_STICK_LY_OFFSET) as *mut f32) = 1.0;
            }
        }
    }
}

/// `Game.Debug::IsEnableControlOnDisactiveWindow` (deobf `0x140e53220`, RE `AUTONOMOUS-FOCUS-FIX-...`):
/// returns false in retail. Its result is cached to `CSPadStep+0xba` every frame; when the ER window
/// is UNFOCUSED and that byte is 0, `CSPadStep::STEP_Update` runs the pad-manager on the "inactive"
/// path that latches a flag which makes the locomotion consumer DISCARD our injected stick (menus still
/// work via the separate DLUID+0x88d gate, but gameplay movement does not). Forcing this to return 1
/// makes the unfocused path byte-identical to the focused one, so the injected pad stick reaches
/// locomotion WITHOUT the window being active -- the missing half of an autonomous, focus-free proof.
const IS_ENABLE_CONTROL_ON_DISACTIVE_RVA: u32 = 0xe53220;

/// Original `IsEnableControlOnDisactiveWindow` (minhook trampoline). 0 until the hook installs. The
/// detour calls this to return the game's REAL value whenever the harness is NOT actively injecting.
static ORIG_IS_ENABLE_CONTROL_ON_DISACTIVE: AtomicUsize = AtomicUsize::new(0);

/// Detour for `IsEnableControlOnDisactiveWindow`. LEAK FIX (bd input-blocking-only-in-harness-during-
/// driving-never-in-product-never-outside-window-2026-07-23): the override that forces "accept control
/// on a disactive/unfocused window" to 1 must exist ONLY while the harness is ACTIVELY INJECTING this
/// frame (the move-probe ON burst / sq-repro driving -- `harness_injection_active()`). Left permanently
/// forced to 1 (the old `-> 1` body, installed for the whole run via `mem::forget`), it made ER process
/// the USER's real mouse/keyboard while the ER window was UNFOCUSED for the ENTIRE run -- the reported
/// live input-lock (run bonky-bean-2: oracle_rawinput_mouse_move_events ~5717 flowed while
/// oracle_window_foreground=False for 459/480 samples). Outside the injection window we now return the
/// game's REAL value (retail: false) via the retained trampoline, so ER accepts control only when
/// focused and the user's input in another window never reaches ER. During the injection window this
/// still returns 1 so the injected forward stick reaches locomotion (injection preserved).
unsafe extern "system" fn is_enable_control_on_disactive_hook(
    a: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    if crate::experiments::harness_injection_active() {
        return 1;
    }
    let orig = ORIG_IS_ENABLE_CONTROL_ON_DISACTIVE.load(Ordering::SeqCst);
    if orig != 0 {
        let f: unsafe extern "system" fn(usize, usize, usize, usize) -> usize =
            unsafe { std::mem::transmute(orig) };
        unsafe { f(a, b, c, d) }
    } else {
        0 // trampoline unavailable -> conservative retail value (control disabled while unfocused)
    }
}

/// Install the "enable control on inactive window" override once (proof runs only). The detour is gated
/// to `harness_injection_active()` and calls the ORIGINAL for the game's real value otherwise, so we
/// MUST retain the trampoline (unlike before, when the detour unconditionally returned 1 and never
/// called through).
fn install_focus_override_hook() {
    static INSTALLED: std::sync::Once = std::sync::Once::new();
    INSTALLED.call_once(|| {
        match unsafe { MH_Initialize() } {
            MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
            status => {
                append_autoload_debug(format_args!(
                    "can-move: focus-override MH_Initialize failed: {status:?}"
                ));
                return;
            }
        }
        let Ok(addr) = crate::game_rva_for_hook(IS_ENABLE_CONTROL_ON_DISACTIVE_RVA) else {
            append_autoload_debug(format_args!("can-move: focus-override game_rva failed"));
            return;
        };
        match unsafe {
            MhHook::new(
                addr as *mut c_void,
                is_enable_control_on_disactive_hook as *mut c_void,
            )
        } {
            Ok(hook) => {
                // Store the trampoline BEFORE enabling so the detour never transmutes an unset sentinel.
                ORIG_IS_ENABLE_CONTROL_ON_DISACTIVE
                    .store(hook.trampoline() as usize, Ordering::SeqCst);
                if unsafe { hook.queue_enable() }.is_ok()
                    && matches!(unsafe { MH_ApplyQueued() }, MH_STATUS::MH_OK)
                {
                    crate::mh::leak_installed_hook(hook);
                    append_autoload_debug(format_args!(
                        "can-move: focus-override installed at 0x{addr:x} (IsEnableControlOnDisactiveWindow->1 ONLY while harness injecting; real value otherwise -- user input never reaches ER while unfocused)"
                    ));
                } else {
                    append_autoload_debug(format_args!("can-move: focus-override enable failed"));
                }
            }
            Err(status) => append_autoload_debug(format_args!(
                "can-move: focus-override MhHook::new failed: {status:?}"
            )),
        }
    });
}

/// Install the pad-poll hook once (only when the movement proof is authorized).
fn install_pad_poll_hook() {
    static INSTALLED: std::sync::Once = std::sync::Once::new();
    INSTALLED.call_once(|| {
        match unsafe { MH_Initialize() } {
            MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
            status => {
                append_autoload_debug(format_args!(
                    "can-move: pad-poll MH_Initialize failed: {status:?}"
                ));
                return;
            }
        }
        let Ok(addr) = crate::game_rva_for_hook(FD4_PAD_DEVICE_POLL_RVA) else {
            append_autoload_debug(format_args!("can-move: pad-poll game_rva failed"));
            return;
        };
        match unsafe { MhHook::new(addr as *mut c_void, pad_poll_hook as *mut c_void) } {
            Ok(hook) => {
                ORIG_PAD_POLL.store(hook.trampoline() as usize, Ordering::SeqCst);
                if unsafe { hook.queue_enable() }.is_ok()
                    && matches!(unsafe { MH_ApplyQueued() }, MH_STATUS::MH_OK)
                {
                    crate::mh::leak_installed_hook(hook);
                    append_autoload_debug(format_args!(
                        "can-move: pad-poll hook installed at 0x{addr:x} (faithful stick injection boundary)"
                    ));
                } else {
                    append_autoload_debug(format_args!("can-move: pad-poll enable failed"));
                }
            }
            Err(status) => append_autoload_debug(format_args!(
                "can-move: pad-poll MhHook::new failed: {status:?}"
            )),
        }
    });
}

/// Previous frame's world position while a probe is active (game thread only touches this).
static PREV_POS: Mutex<Option<(f32, f32, f32)>> = Mutex::new(None);

fn lock_prev() -> std::sync::MutexGuard<'static, Option<(f32, f32, f32)>> {
    PREV_POS.lock().unwrap_or_else(|e| e.into_inner())
}

/// Drive one frame of the can-move probe. Proves HARNESS-driven movement with USER contamination
/// EXCLUDED (user 2026-07-20). It alternates INJECT-ON windows (write the forward stick + hold
/// input-active so it applies unfocused) with INJECT-OFF windows (release the stick), and requires the
/// char to move WHILE WE inject. OFF-tail displacement is retained as diagnostic momentum evidence,
/// not misclassified as foreign input; the proof checker requires the device-boundary suppression
/// oracle to show zero unsuppressed foreign events. Sets HARNESS_MOVE_VERDICT
/// (0 pending / 1 proven / 2 disproven / 3 contaminated) so the watcher tears down the instant the
/// answer is known -- no waiting for an fps/stall window (bd
/// collect-decisive-info-teardown-immediately, canmove-contaminated-user-moved-harness-never-supplied).
pub(crate) fn tick(pos: (f32, f32, f32)) {
    // INJECT-ON / INJECT-OFF window sizes. OFF_TAIL = the last N OFF frames, measured after the char
    // has decelerated, so residual momentum just after releasing the stick isn't miscounted as movement.
    const ON_FRAMES: usize = 30;
    const OFF_FRAMES: usize = 20;
    const CYCLE: usize = ON_FRAMES + OFF_FRAMES;
    const OFF_TAIL: usize = 8;

    // PROOF-ONLY: runs only when the input-harness DLL is present (prove_movement_enabled =
    // GetModuleHandle check, not a marker/env gate); never fires in a normal user session.
    static PROOF_GATE: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);
    pub(crate) use er_telemetry_core::counters::OFF_TAIL_MOVED;
    pub(crate) use er_telemetry_core::counters::OFF_TAIL_TOTAL;
    pub(crate) use er_telemetry_core::counters::ON_MOVED;
    pub(crate) use er_telemetry_core::counters::ON_TOTAL;
    pub(crate) use er_telemetry_core::counters::PHASE_FRAME;

    let gate = PROOF_GATE.load(Ordering::Relaxed);
    let enabled = if gate == 0 {
        let on = crate::experiments::prove_movement_enabled();
        PROOF_GATE.store(if on { 1 } else { 2 }, Ordering::Relaxed);
        on
    } else {
        gate == 1
    };
    if !enabled {
        return;
    }
    install_pad_poll_hook();
    install_focus_override_hook();

    let epoch = SYSTEM_QUIT_CONTINUE_CONFIRM_FRESH_DESER_COUNT.load(Ordering::SeqCst);
    // New load epoch -> reset the probe (each load must re-prove HARNESS movement on its own).
    if MOVE_PROBE_EPOCH.swap(epoch, Ordering::SeqCst) != epoch {
        CAN_MOVE_CONFIRMED.store(false, Ordering::SeqCst);
        HARNESS_MOVE_VERDICT.store(0, Ordering::SeqCst);
        MOVE_PROBE_MOVED_FRAMES.store(0, Ordering::SeqCst);
        DID_MOVE_FRAMES.store(0, Ordering::Relaxed);
        SUPPLIED_MOVEMENT_INPUT_FRAMES.store(0, Ordering::Relaxed);
        PHASE_FRAME.store(0, Ordering::Relaxed);
        ON_TOTAL.store(0, Ordering::Relaxed);
        ON_MOVED.store(0, Ordering::Relaxed);
        OFF_TAIL_TOTAL.store(0, Ordering::Relaxed);
        OFF_TAIL_MOVED.store(0, Ordering::Relaxed);
        MOVE_PROBE_ACTIVE.store(false, Ordering::SeqCst);
        *lock_prev() = None;
    }

    // Do not spend the run's one movement interval during the rendered-but-not-controllable ramp.
    // Arm only after STEP_MoveMap's native resident path has enabled its task group and control bit.
    if !movement_input_ready() {
        MOVE_PROBE_ACTIVE.store(false, Ordering::SeqCst);
        crate::experiments::move_probe_drive_key_foreground_only(0);
        *lock_prev() = None;
        return;
    }

    // Verdict already reached for this load -> stop injecting.
    if HARNESS_MOVE_VERDICT.load(Ordering::SeqCst) != 0 {
        MOVE_PROBE_ACTIVE.store(false, Ordering::SeqCst);
        // Release any held W the moment the verdict latches, so the proof can't walk the char to death.
        crate::experiments::move_probe_drive_key_foreground_only(0);
        return;
    }

    // Hold ER's input-accept flag EVERY frame so the injected stick applies while the window is
    // unfocused (the fix for the discarded 400 injected frames). Never forces foreground.
    hold_input_active();

    let pf = PHASE_FRAME.load(Ordering::Relaxed);
    let is_on = pf < ON_FRAMES;
    // pad_poll_hook overwrites the stick to full-forward ONLY while MOVE_PROBE_ACTIVE. During OFF we
    // leave it false so the real (neutral, unless a user pushes) stick flows through -> the OFF tail
    // measures movement we are NOT causing.
    MOVE_PROBE_ACTIVE.store(is_on, Ordering::SeqCst);
    // NEVER force the window foreground (user 2026-07-23, bd harness-drive-contract-...-no-force-focus):
    // seizing the user's focus is forbidden. Movement is delivered ONLY while ER is ALREADY the foreground
    // window -- the pad-poll/`inject_all_pad_devices` stick and the foreground-only keyboard-W driver below
    // both no-op or auto-release when ER is not focused, so the probe can never steal the user's focus.
    // Also write full-forward to EVERY registered pad device's CONCRETE pointer -- covers the case where
    // the poll hook's `this` is the FD4PadDevice (so `this+0x8a0` is 8 bytes off the real stick) or the
    // player reads a device the poll hook did not fire for this frame.
    if is_on {
        unsafe { inject_all_pad_devices() };
    }
    // KEYBOARD-W movement injection -- THE PROVEN path (bd SWITCH-movement-proof-to-keyboard-W-sendinput):
    // pad-stick / synthetic-xinput never walk the char, but SendInput 'W' via RawInput does, and ER reads
    // gameplay keyboard via RawInput (NOT DInput) so the kb+mouse-disable does not block it. Foreground-
    // only: delivers W only while ER is ALREADY the foreground window (focus is NEVER forced), auto-releases
    // the moment it loses focus, and releases on OFF/verdict so it cannot drive the char to death. Faithful
    // real-input path (not a RAM move-vector cheat). VK 'W' = 0x57.
    crate::experiments::move_probe_drive_key_foreground_only(if is_on { 0x57 } else { 0 });

    let mut prev = lock_prev();
    if let Some((px, _py, pz)) = *prev {
        let dx = pos.0 - px;
        let dz = pos.2 - pz;
        let moved = (dx * dx + dz * dz).sqrt() >= MOVE_PROBE_PER_FRAME_THRESHOLD;
        if is_on {
            ON_TOTAL.fetch_add(1, Ordering::Relaxed);
            if moved {
                ON_MOVED.fetch_add(1, Ordering::Relaxed);
                DID_MOVE_FRAMES.fetch_add(1, Ordering::Relaxed);
                MOVE_PROBE_MOVED_FRAMES.fetch_add(1, Ordering::SeqCst);
            }
        } else if pf >= CYCLE - OFF_TAIL {
            OFF_TAIL_TOTAL.fetch_add(1, Ordering::Relaxed);
            if moved {
                OFF_TAIL_MOVED.fetch_add(1, Ordering::Relaxed);
            }
        }

        // ONE INTERVAL PER LOAD (user 2026-07-23, bd harness-drive-contract-one-move-interval-per-load-...):
        // measure movement across a SINGLE ON burst + OFF tail, then FORCE a terminal verdict at the END of
        // that one cycle -- never loop more intervals waiting for a clean proof. The old cumulative
        // thresholds needed 2-4 cycles to reach a verdict (PROVEN ot>=40, DISPROVEN ot>=90), so a load whose
        // movement never cleanly proved (Bonky) stayed at verdict 0 FOREVER: the probe kept re-injecting
        // (and previously re-forcing focus) and the driver -- gated on the verdict -- never triggered the
        // reload. Now the result (proven/disproven/contaminated) is still RECORDED in telemetry, but after
        // exactly ONE interval a verdict always latches, so the probe stops injecting and the driver advances
        // to the next same-character load REGARDLESS of the result (load -> one interval -> reload -> ...).
        let ot = ON_TOTAL.load(Ordering::Relaxed);
        let om = ON_MOVED.load(Ordering::Relaxed);
        let ft = OFF_TAIL_TOTAL.load(Ordering::Relaxed);
        let fm = OFF_TAIL_MOVED.load(Ordering::Relaxed);
        // The single interval is complete once one full ON+OFF cycle has elapsed (this is its last frame).
        let interval_done = pf + 1 >= CYCLE;
        let verdict = if interval_done {
            // Terminal decision after the one interval. OFF-tail displacement can be ordinary momentum
            // after releasing a proven-forward burst (the resident-gate proof moved on 27/30 ON frames,
            // then continued falling); it cannot identify foreign input. The replay gate separately
            // requires a live device-boundary suppression oracle with zero unsuppressed events.
            if ot > 0 && om * 100 >= 70 * ot {
                1 // PROVEN
            } else {
                2 // DISPROVEN (injection ineffective / char did not clearly move this interval)
            }
        } else {
            0
        };
        if verdict != 0 {
            HARNESS_MOVE_VERDICT.store(verdict, Ordering::SeqCst);
            if verdict == 1 {
                CAN_MOVE_CONFIRMED.store(true, Ordering::SeqCst);
            }
            MOVE_PROBE_ACTIVE.store(false, Ordering::SeqCst);
            let label = match verdict {
                1 => "PROVEN(harness moved char)",
                2 => "DISPROVEN(injection ineffective)",
                _ => "CONTAMINATED(external input)",
            };
            append_autoload_debug(format_args!(
                "can-move: HARNESS_MOVE_VERDICT={verdict} {label} epoch={epoch} on_moved={om}/{ot} off_tail_moved={fm}/{ft}"
            ));
        }
    }
    PHASE_FRAME.store((pf + 1) % CYCLE, Ordering::Relaxed);
    *prev = Some(pos);
}
