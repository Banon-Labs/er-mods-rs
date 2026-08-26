//! `CS::CSFreeListMemorySystem`'s leak assertion, defused so quitting the game does not kill it.
//!
//! # The crash
//!
//! deto's Elden Ring died the same way twice on 2026-08-25 (20:16:43Z and 20:19:55Z), on a profile
//! carrying only this DLL and the crash logger. Unlike every other crash this DLL answers, it is
//! not an access violation: `exception_code=0x80000003` is `STATUS_BREAKPOINT`, and
//! `exception_address == context_rip == eldenring.exe+0xc57676` is a literal `INT3` the game
//! executes on purpose. Both records are identical apart from thread id and stack pointer.
//!
//! # It only happens on the way out
//!
//! `MainLoop` (`0x140c8fe90`) is `while (MainUpdate()) KillWindowTimer();`, and `CleanupUpdate`
//! (`0x140de9f50`) is reached only after that loop ends, in a bounded 200-iteration teardown loop.
//! The call at `0x140c8ff7b` returns to `0x140c8ff80`, which is frame `#14` of deto's crash log. So
//! this is quit-to-desktop: returning to the title, ending an invasion and ending a Seamless
//! session all stay inside `MainUpdate` and cannot reach this code at all.
//!
//! The rest of the chain, from the same log: `CleanupUpdate -> CSTaskImp(0x140eb2180) ->
//! FD4TaskManager(0x142655360) -> 0x14269f970 -> 0x14269f8a0 ->
//! CS::CSHkThreadLocalProcess::~(0x140c57190) -> 0x1416ccf10 -> INT3`. `0x1416ccf10` is what tears
//! down the `CSFreeListMemorySystem` singleton and NULLs its global, so the allocator really is
//! being destroyed here; this is not a per-frame path that merely looks like one.
//!
//! # What the assertion checks
//!
//! `0x140c575e0` is vtable slot 2 of `CS::CSFreeListMemorySystem` -- its RTTI complete object
//! locator at `0x143352958` names `.?AVCSFreeListMemorySystem@CS@@` -- called with flags `3`, which
//! is its free-everything path. It walks a chain of `0x28`-byte nodes linked at `+0x20` and breaks
//! on any node whose byte at `+0x18` is still set:
//!
//! ```text
//! 140c57670: 80 7a 18 00     CMP byte ptr [RDX + 0x18], 0x0
//! 140c57674: 74 01           JZ  0x140c57677
//! 140c57676: cc              INT3
//! 140c57677: 48 8b 4f 08     MOV RCX, [RDI + 0x8]      ; free the node -- on BOTH paths
//! ```
//!
//! That byte is the registrar's "checked out" flag. The registrar is vtable slot 3
//! (`0x140c579f0`): it reuses a node whose `+0x18` is zero, otherwise allocates exactly `0x28`
//! bytes, links it at `+0x20`, and sets `+0x18` to `1`. The sibling debug dump at `0x140c576b0`
//! walks the same chain under the heading `"unused in thread local freelists"`. So a set flag means
//! a thread-local free-list is still checked out while the allocator that owns it is being
//! destroyed -- and deto's minidump holds 52 live threads at that instant, including roughly twenty
//! `eldenring.exe` task workers still parked in `WaitForSingleObject`.
//!
//! # Why removing the `INT3` is safe
//!
//! The `JZ` at `0x140c57674` targets `0x140c57677`, which is `RIP + 1` from the `INT3`. Both the
//! asserting and the non-asserting path continue at the same instruction, and the node is freed
//! either way. The break reports; it does not protect. A debugger would step over it and the
//! function would carry on, which is exactly what this patch makes happen with no debugger
//! attached: `0xcc` becomes `0x90`, and the shutdown takes the path it already takes on every node
//! whose flag is clear.
//!
//! This does not fix the leak, and is not meant to. It cannot: by the time the assertion fires the
//! allocator is already being destroyed and the game frees the node regardless. What it removes is
//! an unhandled `STATUS_BREAKPOINT` that kills the process while it was already quitting.
//!
//! # Evidence this is not ours to begin with
//!
//! Every frame in the chain is `eldenring.exe`, and this DLL's sibling
//! [`null_param_repository`](crate::guards::null_param_repository) guard cannot reach it: that stub
//! tail-jumps to the original unless `GLOBAL_SoloParamRepository` is already null, so it can only
//! act after that singleton is destroyed -- later than any thread-join step. What it did do is
//! remove the *earlier* fatal stop. deto's 2026-08-22 and 2026-08-23 crashes died in this same
//! `CleanupUpdate` phase, on the `DLPanic` that guard now suppresses. The same broken shutdown now
//! simply runs one step further.

use core::sync::atomic::AtomicBool;

use super::Patch;

/// RVA of the `CMP` that opens the assertion, i.e. the first byte of the verified window. The
/// `INT3` itself is [`INT3_OFFSET`] bytes further on.
pub(crate) const FREELIST_SHUTDOWN_ASSERT_RVA: usize = 0xc5_7670;

/// Index of the `INT3` within the window: past `CMP byte ptr [RDX+0x18],0` (4 bytes) and `JZ +1`
/// (2 bytes).
const INT3_OFFSET: usize = 6;

/// `NOP`. One byte, like the `INT3` it replaces, so nothing after it moves and the `JZ` that jumps
/// over it still lands on the instruction it always landed on.
const NOP: u8 = 0x90;

/// Set once the byte has been written and read back as [`NOP`].
static ASSERT_DEFUSED: AtomicBool = AtomicBool::new(false);

// The expected window, assembled from named `iced-x86` instructions by this crate's `build.rs` and
// verified there against `eldenring-deobf.bin` when a copy is present.
include!(concat!(
    env!("OUT_DIR"),
    "/generated_freelist_shutdown_assert.rs"
));

pub(crate) const FREELIST_SHUTDOWN_ASSERT: Patch = Patch {
    name: "CSFreeListMemorySystem shutdown leak assert",
    rva: FREELIST_SHUTDOWN_ASSERT_RVA,
    expected_window: FREELIST_SHUTDOWN_ASSERT_WINDOW,
    offset: INT3_OFFSET,
    replacement: NOP,
    applied: &ASSERT_DEFUSED,
    rationale: "the assert's own JZ targets RIP+1, so both paths converge on the MOV that frees \
                the node; replacing the INT3 with a NOP is the path the game already takes when \
                the flag is clear, and turns an unhandled STATUS_BREAKPOINT at quit into a clean \
                exit",
};

#[cfg(test)]
mod tests {
    use super::*;

    /// The window is what pins the address, so its shape is worth asserting rather than trusting.
    #[test]
    fn window_is_the_assert_idiom() {
        assert_eq!(
            FREELIST_SHUTDOWN_ASSERT_WINDOW,
            &[
                0x80, 0x7a, 0x18, 0x00, 0x74, 0x01, 0xcc, 0x48, 0x8b, 0x4f, 0x08
            ],
            "the 1.16.2 bytes of CMP/JZ/INT3/MOV at 0x140c57670"
        );
    }

    #[test]
    fn the_patched_byte_is_the_int3() {
        assert_eq!(
            FREELIST_SHUTDOWN_ASSERT.replaced(),
            Some(0xcc),
            "the offset must name the INT3, not one of the instructions that pin it"
        );
    }

    /// `JZ +1` is the entire safety argument: it proves the break is skippable by design.
    #[test]
    fn the_jump_lands_one_byte_past_the_int3() {
        let jump_displacement = FREELIST_SHUTDOWN_ASSERT_WINDOW[INT3_OFFSET - 1];
        assert_eq!(
            usize::from(jump_displacement),
            1,
            "the JZ must skip exactly the INT3; a different displacement means this is not the \
             advisory-break idiom the patch is argued from"
        );
    }
}
