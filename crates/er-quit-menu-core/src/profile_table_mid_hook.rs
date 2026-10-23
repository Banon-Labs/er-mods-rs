//! The profile-renderer refresh, guarded at the one read of a table slot that may be null.
//!
//! # The crash
//!
//! The native refresh ([`er_loading_portrait_core::PROFILE_RENDERER_REFRESH_RVA`], 2.7.2.0
//! `0x1409abcd0`) walks the ten-slot renderer table and, for every slot whose profile record exists,
//! reads the slot's request latches without testing the slot first:
//!
//! ```text
//! +0x30: MOV RDI, qword ptr [RSI]              ; table[slot]
//! +0x38: CALL <profile record>                  ; JE +0xce if none
//! +0x4b: CALL <slot is shown>                   ; JE +0xce if not
//! +0x54: CMP byte ptr [RDI + 0x754], 0          ; RDI is null -> er-effects-rs-j3r
//! +0x5b: JNE +0xce
//! +0x5d: CMP byte ptr [RDI + 0x755], 0
//! ...    the per-slot draw requests on RDI
//! +0xce: INC EBX; ADD RSI,8; CMP EBX,10; JB +0x30
//! ```
//!
//! Offsets are the same in 1.16.2 (`0x1409aa680`), 1.17.1 and 2.7.2.0 (`0x1409abcd0`): the body is
//! the same instruction for instruction in all three images (read 2026-10-19 and 2026-10-23).
//!
//! # Why a mid hook and not only the entry guard
//!
//! [`crate::profile_table_guard`] skips the whole refresh when any slot is null, which also drops
//! every valid slot's portrait request for that call. This hook skips only the slot being walked,
//! by taking the loop's own `+0xce` join -- the branch the three tests above already take for a
//! slot that has nothing to draw. With it armed, the entry guard only diagnoses.
//!
//! # What the join reads
//!
//! `+0xce` reads `EBX` (slot index) and `RSI` (table cursor), both loop state in callee-saved
//! registers the skipped code does not write. `RDI` and `RBP` are reloaded at `+0x30` before the
//! next slot reads them, `R14` (the profile summary) is untouched, and the flags are written by
//! `INC` before anything reads them. So the routine changes no register; it only picks the address.
//!
//! No branch in the refresh lands inside the 16-byte window `+0x54..+0x64`: its branch targets are
//! `+0x30` and `+0xce` only, checked against the disassembly of each image (1.16.2 and 1.17.1 on
//! 2026-10-19, 2.7.2.0 on 2026-10-23) by collecting every jump target in the function. The window
//! holds a rel8 `JNE`, which ilhook 2.3 relocates either through `iced_x86::BlockEncoder`
//! (trampoline within 2 GB, short branches widened as needed) or by negating the condition over an absolute jump (`move_inst::relocate_inst_addr`).
//!
//! # One installer for two hosts
//!
//! The product and this crate each detour the refresh entry from their own installer, and the two
//! never share a process. Both call [`install_profile_table_mid_hook`], which arms once per process,
//! so `+0x54` is written by exactly one hook whichever host is loaded. The entry detours cover the
//! first bytes of the function and this window starts at `+0x54`, so the two never overlap.

// The installer is game-target only; on the host the routine and its windows exist for the tests.
#![cfg_attr(not(windows), allow(dead_code))]

use core::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

/// The refresh, 2.7.2.0 RVA of its `.pdata` entry (`0x9abcd0`). Its declaring module is
/// game-target only, and so is the installer that reads it.
#[cfg(windows)]
pub const PROFILE_RENDERER_REFRESH_FUNCTION_RVA: usize =
    er_loading_portrait_core::PROFILE_RENDERER_REFRESH_RVA;
/// `CMP byte [RDI+0x754],0`, the first read of `table[slot]`.
pub const PROFILE_RENDERER_REFRESH_LATCH_CHECK: usize = 0x54;
/// `INC EBX`, the loop's skip-this-slot join.
pub const PROFILE_RENDERER_REFRESH_NEXT_SLOT: usize = 0xce;

/// Absolute `+0xce` on the running build. Zero until resolved, and a routine that reads zero takes
/// the original path.
static NEXT_SLOT_RESUME: AtomicUsize = AtomicUsize::new(0);
/// The `CSMenuProfModelRend` vtable on the running build, the same test `scan_table` applies.
static RENDERER_VTABLE: AtomicUsize = AtomicUsize::new(0);
/// Slots the routine skipped. Like a guard's counter, a nonzero value is the proof a run met a
/// null slot.
pub static PROFILE_TABLE_MID_HOOK_DIVERTED: AtomicU64 = AtomicU64::new(0);

const STATE_UNINSTALLED: usize = 0;
const STATE_INSTALLING: usize = 1;
const STATE_ARMED: usize = 2;
const STATE_FAILED: usize = 3;
static STATE: AtomicUsize = AtomicUsize::new(STATE_UNINSTALLED);

include!(concat!(
    env!("OUT_DIR"),
    "/generated_profile_table_mid_hook_windows.rs"
));

/// Whether the hook is live in this process. When it is, a null slot costs only that slot.
pub fn profile_table_mid_hook_armed() -> bool {
    STATE.load(Ordering::Acquire) == STATE_ARMED
}

/// Whether the slot in `RDI` must be skipped: null, unreadable, or not a renderer.
///
/// `read` is the fault-safe qword reader; it is a parameter so the decision is testable without a
/// game.
fn slot_must_be_skipped(
    renderer: usize,
    want_vtable: usize,
    read: impl FnOnce(usize) -> Option<usize>,
) -> bool {
    renderer == 0 || read(renderer) != Some(want_vtable)
}

#[cfg(windows)]
fn read_qword(address: usize) -> Option<usize> {
    unsafe { er_game_base::mem::safe_read_usize(address) }
}

/// Host builds read the test's own memory directly; there is no `ReadProcessMemory` to call.
#[cfg(not(windows))]
fn read_qword(address: usize) -> Option<usize> {
    Some(unsafe { core::ptr::read_volatile(address as *const usize) })
}

/// `+0x54`: a slot that is not a renderer -> the next-slot join at `+0xce`.
///
/// # Safety
///
/// Installed by ilhook as the jmp-ret routine at `+0x54`; never called from Rust outside tests.
/// `RDI` holds `table[slot]`, loaded at `+0x30` and preserved across the two calls in between.
unsafe extern "win64" fn latch_check_route(
    regs: *mut ilhook::x64::Registers,
    original: usize,
    _user_data: usize,
) -> usize {
    let resume = NEXT_SLOT_RESUME.load(Ordering::Acquire);
    let want_vtable = RENDERER_VTABLE.load(Ordering::Acquire);
    if resume == 0 || want_vtable == 0 {
        return original;
    }
    let renderer = unsafe { (*regs).rdi } as usize;
    if !slot_must_be_skipped(renderer, want_vtable, read_qword) {
        return original;
    }
    PROFILE_TABLE_MID_HOOK_DIVERTED.fetch_add(1, Ordering::Relaxed);
    resume
}

/// Arm the mid hook once per process. Returns whether it is armed.
///
/// Both addresses are offsets into the refresh, so both go through
/// [`er_game_base::game_build::resolve_call_site_rva`], and both windows are checked against the
/// bytes `build.rs` assembled before anything is written. The resume address and the vtable are
/// published before the hook goes live.
///
/// # Safety
///
/// Process attach or startup-hook context. Writes a jump into game code.
#[cfg(windows)]
pub unsafe fn install_profile_table_mid_hook() -> bool {
    use crate::host::append_autoload_debug;
    use ilhook::x64::{CallbackOption, HookFlags, HookType, Hooker};

    match STATE.compare_exchange(
        STATE_UNINSTALLED,
        STATE_INSTALLING,
        Ordering::AcqRel,
        Ordering::Acquire,
    ) {
        Ok(_) => {}
        Err(state) => return state == STATE_ARMED,
    }
    let name = "profile-renderer refresh table-slot read";
    let fail = || {
        STATE.store(STATE_FAILED, Ordering::Release);
        false
    };
    let Ok(base) = er_game_base::mem::game_module_base() else {
        return fail();
    };
    // Named `at_offset`, not `resolve`: these are offsets into the refresh, not game addresses.
    let at_offset = |offset: usize| {
        er_game_base::game_build::resolve_call_site_rva(
            PROFILE_RENDERER_REFRESH_FUNCTION_RVA,
            offset,
            name,
        )
        .map(|rva| base + rva)
    };
    let (Some(site), Some(resume)) = (
        at_offset(PROFILE_RENDERER_REFRESH_LATCH_CHECK),
        at_offset(PROFILE_RENDERER_REFRESH_NEXT_SLOT),
    ) else {
        append_autoload_debug(format_args!(
            "profileselect-table-midhook: REFUSED {name} (fn 0x{PROFILE_RENDERER_REFRESH_FUNCTION_RVA:x} + 0x{PROFILE_RENDERER_REFRESH_LATCH_CHECK:x}, resume + 0x{PROFILE_RENDERER_REFRESH_NEXT_SLOT:x}): the refresh is only measured on 2.7.2.0 -- {}. Nothing read, nothing written; the entry guard still skips a degraded refresh whole.",
            er_game_base::game_build::describe_build()
        ));
        return fail();
    };
    for (label, address, expected) in [
        ("site", site, PROFILE_REFRESH_LATCH_CHECK_WINDOW),
        ("resume", resume, PROFILE_REFRESH_NEXT_SLOT_WINDOW),
    ] {
        let mut actual = [0_u8; 16];
        let window = &mut actual[..expected.len()];
        if !unsafe { er_game_base::mem::read_bytes(address, window) } || window != expected {
            append_autoload_debug(format_args!(
                "profileselect-table-midhook: DISARMED {name} {label} @0x{address:x}: byte mismatch (got {window:02x?}, want {expected:02x?}) -- the address was resolved for the running build, so the likeliest cause is that another mod rewrote these bytes. Not installing; the entry guard still skips a degraded refresh whole."
            ));
            return fail();
        }
    }
    let want_vtable = er_game_base::mem::game_data_addr(
        base,
        er_loading_portrait_core::TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA,
        "TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA",
    );
    RENDERER_VTABLE.store(want_vtable, Ordering::Release);
    NEXT_SLOT_RESUME.store(resume, Ordering::Release);
    let hooker = Hooker::new(
        site,
        HookType::JmpToRet(latch_check_route),
        CallbackOption::None,
        0,
        HookFlags::empty(),
    );
    match unsafe { hooker.hook() } {
        Ok(point) => {
            // The hook lives as long as the process; dropping the point would unhook it.
            core::mem::forget(point);
            STATE.store(STATE_ARMED, Ordering::Release);
            append_autoload_debug(format_args!(
                "profileselect-table-midhook: ARMED {name} @0x{site:x} (resume 0x{resume:x}, vtable 0x{want_vtable:x}; fn 0x{PROFILE_RENDERER_REFRESH_FUNCTION_RVA:x} + 0x{PROFILE_RENDERER_REFRESH_LATCH_CHECK:x}): a null or non-renderer slot takes the loop's own next-slot join (+0x{PROFILE_RENDERER_REFRESH_NEXT_SLOT:x}); the other slots still get their portrait requests (er-effects-rs-j3r)"
            ));
            true
        }
        Err(err) => {
            NEXT_SLOT_RESUME.store(0, Ordering::Release);
            append_autoload_debug(format_args!(
                "profileselect-table-midhook: DISARMED {name} @0x{site:x}: ilhook refused the hook: {err}; the entry guard still skips a degraded refresh whole"
            ));
            fail()
        }
    }
}

/// The diversion count, for the entry guard's per-refresh line.
pub fn profile_table_mid_hook_diverted() -> u64 {
    PROFILE_TABLE_MID_HOOK_DIVERTED.load(Ordering::Relaxed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offsets_match_static_re() {
        #[cfg(windows)]
        assert_eq!(PROFILE_RENDERER_REFRESH_FUNCTION_RVA, 0x9a_bcd0);
        assert_eq!(
            (
                PROFILE_RENDERER_REFRESH_LATCH_CHECK,
                PROFILE_RENDERER_REFRESH_NEXT_SLOT
            ),
            (0x54, 0xce)
        );
    }

    /// ilhook writes at most 14 bytes, so the window must cover that much and end on an
    /// instruction boundary: the three moved instructions are 16 bytes.
    #[test]
    fn window_covers_the_jump() {
        const ILHOOK_JUMP_BYTES: usize = 14;
        assert!(PROFILE_REFRESH_LATCH_CHECK_WINDOW.len() >= ILHOOK_JUMP_BYTES);
        assert_eq!(PROFILE_REFRESH_LATCH_CHECK_WINDOW.len(), 16);
        assert_eq!(PROFILE_REFRESH_NEXT_SLOT_WINDOW.len(), 9);
    }

    #[test]
    fn the_skip_decision_matches_scan_table() {
        let vtable = 0x1_4abc_0128;
        assert!(slot_must_be_skipped(0, vtable, |_| unreachable!(
            "null is never read"
        )));
        assert!(slot_must_be_skipped(0x5000, vtable, |_| None), "unreadable");
        assert!(
            slot_must_be_skipped(0x5000, vtable, |_| Some(0x1234)),
            "not a renderer"
        );
        assert!(!slot_must_be_skipped(0x5000, vtable, |_| Some(vtable)));
    }

    /// One test drives every state of the shared statics in order, so parallel tests cannot race
    /// on them.
    #[test]
    fn the_routine_skips_only_a_slot_that_is_not_a_renderer() {
        let vtable: usize = 0x1_4abc_0128;
        let renderer: [usize; 2] = [vtable, 0];
        let stranger: [usize; 2] = [0xdead, 0];
        let mut regs: ilhook::x64::Registers = unsafe { core::mem::zeroed() };

        NEXT_SLOT_RESUME.store(0, Ordering::SeqCst);
        RENDERER_VTABLE.store(vtable, Ordering::SeqCst);
        let back = unsafe { latch_check_route(&mut regs, 0x1234, 0) };
        assert_eq!(
            back, 0x1234,
            "an unresolved resume address must never be jumped to"
        );

        NEXT_SLOT_RESUME.store(0x5000, Ordering::SeqCst);
        let before = profile_table_mid_hook_diverted();
        regs.rdi = 0;
        assert_eq!(unsafe { latch_check_route(&mut regs, 0x1234, 0) }, 0x5000);
        regs.rdi = stranger.as_ptr() as u64;
        assert_eq!(unsafe { latch_check_route(&mut regs, 0x1234, 0) }, 0x5000);
        regs.rdi = renderer.as_ptr() as u64;
        assert_eq!(unsafe { latch_check_route(&mut regs, 0x1234, 0) }, 0x1234);
        assert_eq!(profile_table_mid_hook_diverted() - before, 2);
        assert_eq!(
            regs.rdi,
            renderer.as_ptr() as u64,
            "the routine changes no register"
        );

        NEXT_SLOT_RESUME.store(0, Ordering::SeqCst);
        RENDERER_VTABLE.store(0, Ordering::SeqCst);
    }
}
