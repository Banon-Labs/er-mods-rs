//! The mid-hook registry.
//!
//! A *mid hook* intercepts one instruction inside a function, not its entry. It is the tool for a
//! fault raised partway through a function whose other work must still happen: a guard at the
//! entry can only run the whole function or none of it, and a one-byte [patch](crate::patches)
//! cannot add a test.
//!
//! The mechanism is [`ilhook`]'s jmp-ret hook. It writes a 14-byte absolute jump over the hooked
//! instructions, moves those instructions into a trampoline, saves every register into a
//! [`Registers`](ilhook::x64::Registers) block, and calls a routine that may change any of them and
//! returns the address to continue at. Returning the trampoline (`ori_func_ptr`) runs the moved
//! instructions and falls back into the function, exactly as if nothing had been hooked; returning
//! any other address takes that branch instead, with the edited registers restored.
//!
//! # Rules a new mid hook must satisfy
//!
//! 1. **Resume on a branch the function already has.** The alternative address must be a join
//!    point the original code reaches itself, and the routine must leave every register that join
//!    reads as the original path would. Name each such register in the hook's module.
//! 2. **Pin the whole moved window.** The jump covers 14 bytes, so every instruction it touches is
//!    relocated. The expected window is those instructions in full, generated from named
//!    instructions by `build.rs` and checked against the live image before anything is written.
//!    No branch in the function may land inside the window; the module records how that was checked.
//! 3. **Pin the resume target too.** The routine jumps to a second address the hook never wrote, so
//!    its bytes are checked as well. A resume target that moved fails closed rather than sending a
//!    thread into the middle of an instruction.
//! 4. **Resolve as call sites, never as detours.** Both addresses are an offset into a function, so
//!    they go through [`er_game_base::game_build::resolve_call_site_rva`], which maps the enclosing
//!    function and adds the offset afterwards. `resolve_detour_address` refuses mid-function
//!    addresses on purpose.

use core::sync::atomic::{AtomicU64, AtomicUsize};

pub(crate) mod give_items;

/// One hooked instruction and the branch it can divert to.
pub(crate) struct MidHook {
    /// Name used in the install log and the telemetry line.
    pub(crate) name: &'static str,
    /// 1.16.2 RVA of the `.pdata` function entry that contains both addresses.
    pub(crate) function_rva: usize,
    /// Offset of the hooked instruction within [`Self::function_rva`].
    pub(crate) hook_offset: usize,
    /// The instructions the 14-byte jump overwrites, checked before hooking.
    pub(crate) expected_window: &'static [u8],
    /// Offset of the branch the routine may divert to.
    pub(crate) resume_offset: usize,
    /// Bytes at the resume target, checked before hooking.
    pub(crate) expected_resume: &'static [u8],
    /// The jmp-ret routine.
    pub(crate) routine: ilhook::x64::JmpToRetRoutine,
    /// Absolute resume address, published before the hook is enabled. Zero means unresolved, and a
    /// routine that reads zero takes the original path.
    pub(crate) resume: &'static AtomicUsize,
    /// Times the routine diverted. Like a guard's counter, this is the proof a run met the bug.
    pub(crate) diverted: &'static AtomicU64,
    /// Why the diversion is the right answer, printed at install time.
    pub(crate) rationale: &'static str,
}

/// Every mid hook this DLL installs.
pub(crate) static REGISTRY: &[MidHook] = &[give_items::STORAGE_OVERFLOW, give_items::PICKUP_LOG];
