//! THE THUNK MANIPULATOR: a 55-slot `ChrManipulator` whose vtable forwards 53 slots to the
//! creature's real `ComManipulator`, no-ops its AI brain, and refuses to destroy anything.
//!
//! # What it is for
//!
//! `ChrCtrl+0x3b0` is a manipulator OVERRIDE slot. Three dispatchers check it before falling back
//! to `ChrCtrl+0x18` -- the think path (`ChrCtrl::UpdateAi`, `[vt+0x48]`), the per-frame tick
//! dispatcher (`[vt+0x50]` or `[vt+0x40]`, then `[vt+0xf8]`), and a third at `[vt+0x68]`. Retail
//! never writes the slot and never frees what is in it, so writing our own object there routes
//! those dispatchers at us while everything else in the engine keeps finding the real manipulator
//! through `ChrCtrl+0x18`.
//!
//! That split is the entire mechanism. **Think** and **execute** live in two different vtable
//! slots, so no-oping one and forwarding the other buys AI-free locomotion with no other change:
//! `+0x48` is `if (aiIns && ShouldUpdateAi) AiIns::Update(aiIns, dt)` and nothing else -- goal
//! selection, target acquisition, path replanning, AI timers -- while `+0x50` runs
//! `AiIns::UpdateMovement` and then consumes `walkType`/`wantToMoveTo` into actual movement.
//! Damage, animation and physics are not in the manipulator at all; they hang off
//! `ChrIns::Update`, which is untouched.
//!
//! # THE DIVERGENCE THIS DESIGN CREATES, MEASURED 2026-09-02, AND STILL PRESENT
//!
//! The `rcx` swap below is what makes a 16-byte stub possible, and it is also why a possessed
//! creature cannot walk. Swapping `rcx` means every forwarded slot RUNS ON the real
//! `ComManipulator` and writes its fields there. But the engine does not only dispatch through
//! `ChrCtrl+0x3b0` -- it also READS FIELDS off whatever that resolution returns, in fourteen
//! places, all shaped `chrManipulator ?? manipulator`:
//!
//! ```text
//! 0x1403c8fef  FUN_1403c8fd0   <- PreBehaviorSafe@140401bd0
//! 0x1403cc060  FUN_1403cbff0   <- FUN_1403c8da0 <- FUN_1404016d0   (and again at 0x1403cc160)
//! 0x1403c8c44  UpdateAiLogic@140400960
//! 0x1403c8647  updatePos@1403c8610                                 (and twice more inside it)
//! ...
//! ```
//!
//! `FUN_1403cbff0` reads the published move vector at `ChrManipulator+0x70` (`0x1403cc0b9`) off
//! exactly that pointer. `[vt+0x50]` publishes it into the REAL object; the consumer reads it out
//! of the THUNK, which is zeroes. Runtime telemetry: `staged == published` on the real object,
//! `rootMotion2 == 0`, body pinned to five decimals. The vector is perfect and nobody reads it.
//!
//! The object identity has to stop diverging, and the way to do that is to stop having two
//! objects: swizzle the REAL `ComManipulator`'s vptr to a patched copy of its own vtable with
//! `+0x48` no-oped, and leave `ChrCtrl+0x3b0` NULL. The `rcx` swap then has nothing to swap and
//! the stubs become `jmp [origVtable + N]` with `rcx` untouched. **Hazard:** on the real object,
//! `+0x08` must forward to the true destructor instead of returning `this` -- the engine really
//! does destroy that object, and `ReturnThis` there would leak it.
//!
//! # How a forwarding stub can be sixteen bytes
//!
//! Every call site in the engine dispatches identically:
//!
//! ```text
//! mov rax, [rdi]      ; the object's vptr
//! mov rcx, rdi        ; `this`
//! call [rax+N]        ; the slot
//! ```
//!
//! so the ONLY thing the engine reads off our object before entering a slot is `[obj+0]`, and
//! every forwarded body then dereferences its own `this`. A stub therefore only has to swap `rcx`
//! for the real `ComManipulator` and tail-jump through the real vtable. Arguments in `rdx`/`r8`/
//! `r9`/`xmm` are untouched, `rax` is a volatile scratch register in the Microsoft x64 ABI, and a
//! `jmp` leaves the return address exactly where the caller put it -- so the real function returns
//! straight to the engine and no frame of ours exists at all.
//!
//! # THE TWO SLOTS THAT MUST NOT FORWARD
//!
//! * **`+0x08`, the scalar deleting destructor.** `ComManipulator`'s calls
//!   `operator delete(this, 0x170)`. With `rcx` swapped, that frees THE CREATURE'S REAL
//!   MANIPULATOR out from under the engine. Ours returns `this` and does nothing, which is also
//!   the correct lifetime answer: nothing in retail calls `[manip+8]` on the override slot, so the
//!   object is ours to free and the engine never asks.
//! * **`+0x48`, `UpdateAi`.** No-oped on purpose; that is the feature.
//!
//! # Why 55 slots and not the five that are demonstrably reached
//!
//! 55 slots = `0x1B8` bytes, agreed by three independent oracles: an `.rdata` COL-boundary scan
//! measuring every one of the eight manipulator vtables at exactly `0x1b8`; Ghidra's curated
//! `ChrManipulator_vtable` struct at 55 fields / 440 bytes; and both game images separately. 36 of
//! the 55 are demonstrably live on an override manipulator, and a global byte scan finds real
//! `call`/`jmp [reg+N]` sites for ALL 55 -- so no offset can be ruled out by absence. An
//! under-sized table is not a smaller feature, it is a shipped crash on whichever slot nobody
//! happened to exercise.
//!
//! # Why the object is `0x170` zeroed bytes
//!
//! Field layout is irrelevant while `rcx` is swapped -- with ONE exception. The tick dispatcher
//! does a DIRECT field read, `mov 0xb0(%rdi),%edx`, on the object it dispatched through. It is
//! gated behind `[vt+0xf8]` returning true, and `ComManipulator`'s `+0xf8` is `xor al,al; ret`, so
//! today it is unreachable. Sixteen bytes would work today and be one engine branch away from
//! reading garbage. Mirroring the real `operator delete` size costs a third of a page.
//!
//! The real-`ComManipulator` back-pointer therefore lives at `+0x170`, deliberately OUTSIDE
//! anything the engine believes exists.

// The emitter below is pure byte arithmetic and stays ungated so its tests run on the host; the
// allocation and install half is `#[cfg(windows)]`.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::possess::layout::manipulator::COM_SIZE;

/// Slots in a `ChrManipulator` vtable. See the module docs for the three oracles.
pub(crate) const SLOT_COUNT: usize = 55;

/// Bytes in that vtable. Named separately from `SLOT_COUNT * 8` because it is the number the three
/// oracles actually measured, and a test asserts the two agree.
pub(crate) const VTABLE_BYTES: usize = 0x1b8;

/// `[vt+0x08]` -- the scalar deleting destructor. MUST NOT FORWARD.
pub(crate) const SLOT_DESTRUCTOR: usize = 0x08;

/// `[vt+0x48]` -- `UpdateAi`. The slot that is no-oped, and the reason the mod works.
pub(crate) const SLOT_UPDATE_AI: usize = 0x48;

/// Byte offset of the real-`ComManipulator` back-pointer inside our object.
///
/// Equal to [`COM_SIZE`] on purpose: the engine believes the object is that many bytes, so this is
/// the first address past everything it can think it owns.
pub(crate) const BACK_POINTER_OFFSET: usize = COM_SIZE;

/// Bytes in the thunk object: the mirrored `ComManipulator` plus the back-pointer.
pub(crate) const OBJECT_BYTES: usize = BACK_POINTER_OFFSET + core::mem::size_of::<usize>();

/// Bytes reserved per stub. The longest stub emitted is 16; the rest are padded to the same
/// stride so a slot's code address is `stubs_base + index * STUB_STRIDE` with no table lookup.
pub(crate) const STUB_STRIDE: usize = 16;

/// `int3`. Pads every stub out to [`STUB_STRIDE`] so a fall-through off the end of one lands on a
/// breakpoint rather than running headlong into the next slot's code.
const INT3: u8 = 0xcc;

/// What one vtable slot does.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SlotKind {
    /// Swap `rcx` to the real `ComManipulator` and tail-jump through its vtable at the same
    /// offset.
    Forward,
    /// `xor eax,eax; ret`. Used for `UpdateAi`: the callers treat it as `void`, and returning zero
    /// is also the right answer for any of them that reads `al`.
    NoOp,
    /// `mov rax,rcx; ret`. The scalar deleting destructor contract is "return `this`", and doing
    /// exactly that without deleting is what keeps the creature's real manipulator alive.
    ReturnThis,
}

/// What slot at `offset` does. The whole policy, in one place, so the table and the tests read the
/// same rule rather than agreeing by coincidence.
#[must_use]
pub(crate) const fn slot_kind(offset: usize) -> SlotKind {
    match offset {
        SLOT_DESTRUCTOR => SlotKind::ReturnThis,
        SLOT_UPDATE_AI => SlotKind::NoOp,
        _ => SlotKind::Forward,
    }
}

/// The machine code for one slot, padded to [`STUB_STRIDE`].
///
/// `offset` is the slot's byte offset in the vtable, i.e. `index * 8`.
#[must_use]
pub(crate) fn stub_bytes(offset: usize) -> [u8; STUB_STRIDE] {
    let mut out = [INT3; STUB_STRIDE];
    let code: &[u8] = match slot_kind(offset) {
        // 48 8b c1  mov rax, rcx
        // c3        ret
        SlotKind::ReturnThis => &[0x48, 0x8b, 0xc1, 0xc3],
        // 31 c0     xor eax, eax
        // c3        ret
        SlotKind::NoOp => &[0x31, 0xc0, 0xc3],
        SlotKind::Forward => {
            // 48 8b 89 <disp32=BACK_POINTER_OFFSET>  mov rcx, [rcx + 0x170]   ; the real Com
            // 48 8b 01                               mov rax, [rcx]           ; its vptr
            // ff a0 <disp32=offset>                  jmp qword ptr [rax + N]  ; tail call
            let back = (BACK_POINTER_OFFSET as u32).to_le_bytes();
            let slot = (offset as u32).to_le_bytes();
            out[0] = 0x48;
            out[1] = 0x8b;
            out[2] = 0x89;
            out[3..7].copy_from_slice(&back);
            out[7] = 0x48;
            out[8] = 0x8b;
            out[9] = 0x01;
            out[10] = 0xff;
            out[11] = 0xa0;
            out[12..16].copy_from_slice(&slot);
            return out;
        }
    };
    out[..code.len()].copy_from_slice(code);
    out
}

/// The whole stub block, one [`STUB_STRIDE`]-byte entry per slot, in vtable order.
#[must_use]
pub(crate) fn stub_block() -> Vec<u8> {
    let mut out = Vec::with_capacity(SLOT_COUNT * STUB_STRIDE);
    for index in 0..SLOT_COUNT {
        out.extend_from_slice(&stub_bytes(index * core::mem::size_of::<usize>()));
    }
    out
}

/// The vtable, given the address the stub block will live at.
///
/// Separate from [`stub_block`] so both are testable without a `VirtualAlloc`: hand it any base
/// and check the arithmetic.
#[must_use]
pub(crate) fn vtable_for(stubs_base: usize) -> Vec<usize> {
    (0..SLOT_COUNT)
        .map(|index| stubs_base + index * STUB_STRIDE)
        .collect()
}

/// Where each piece sits inside the single page the thunk is built in.
///
/// One allocation for all three because they have the same lifetime and because a
/// `PAGE_EXECUTE_READWRITE` page satisfies all three requirements at once: the object must be
/// writable, the vtable readable, the stubs executable.
pub(crate) mod plan {
    use super::{OBJECT_BYTES, SLOT_COUNT, STUB_STRIDE, VTABLE_BYTES};

    /// The thunk object, first, so its address IS the allocation's address.
    pub(crate) const OBJECT_AT: usize = 0;
    /// Bytes reserved for the object: [`OBJECT_BYTES`] (`0x178`) rounded up to 16.
    ///
    /// A LITERAL rather than `OBJECT_BYTES.next_multiple_of(16)`, and the assertions below are
    /// what make that safe. The repo's `scripts/rva_symbols.py` resolver evaluates the constants
    /// in this tree to decide whether a given address is claimed by any of them, and a constant it
    /// CANNOT evaluate becomes wide residue -- it might be anything, so no address can be proven
    /// unclaimed and the audits built on that lose the ability to say "nothing declares this".
    /// `next_multiple_of` is a method call, which that resolver does not evaluate, and it made
    /// three constants here unresolvable and the resolver's own selftest go red. The value is
    /// pinned instead, and the two `const` assertions turn any drift into a build error.
    pub(crate) const OBJECT_REGION_BYTES: usize = 0x180;
    /// The vtable, 16-aligned past the object.
    pub(crate) const VTABLE_AT: usize = OBJECT_AT + OBJECT_REGION_BYTES;
    /// The stub block.
    pub(crate) const STUBS_AT: usize = VTABLE_AT + VTABLE_BYTES;
    /// Total bytes to reserve.
    pub(crate) const TOTAL_BYTES: usize = STUBS_AT + SLOT_COUNT * STUB_STRIDE;

    /// The object region must hold the whole object and leave the vtable 16-aligned.
    const _: () = assert!(OBJECT_REGION_BYTES >= OBJECT_BYTES);
    const _: () = assert!(OBJECT_REGION_BYTES.is_multiple_of(16));
    /// ...and one rounding step is all it may be, or the layout has silently grown a hole.
    const _: () = assert!(OBJECT_REGION_BYTES - OBJECT_BYTES < 16);
}

#[cfg(windows)]
pub(crate) use windows_impl::Thunk;

#[cfg(windows)]
mod windows_impl {
    use super::{OBJECT_BYTES, SLOT_COUNT, plan, stub_block, vtable_for};
    use crate::possess::layout::manipulator::COM_SIZE;

    unsafe extern "system" {
        fn VirtualAlloc(
            address: *mut core::ffi::c_void,
            size: usize,
            allocation_type: u32,
            protect: u32,
        ) -> *mut core::ffi::c_void;
        fn VirtualFree(address: *mut core::ffi::c_void, size: usize, free_type: u32) -> i32;
    }

    const MEM_COMMIT: u32 = 0x1000;
    const MEM_RESERVE: u32 = 0x2000;
    const MEM_RELEASE: u32 = 0x8000;
    const PAGE_EXECUTE_READWRITE: u32 = 0x40;

    /// A live thunk manipulator: one page holding the object, its vtable and its 55 stubs.
    ///
    /// # Lifetime
    ///
    /// Owned by us and by nothing else -- retail never calls `[manip+8]` on the override slot, so
    /// the engine will never try to delete it. Dropping this frees the page, and the ONLY safe
    /// moment to do that is after `ChrCtrl+0x3b0` has been nulled; the teardown state machine in
    /// [`crate::possess::teardown`] is what enforces that order.
    #[derive(Debug)]
    pub(crate) struct Thunk {
        page: *mut u8,
    }

    // The page is reached only from the game task thread, and the raw pointer is the reason the
    // compiler cannot see that. `PossessionEngine` requires `Send`, so say it explicitly rather
    // than storing a `usize` and casting at every use.
    unsafe impl Send for Thunk {}

    impl Thunk {
        /// Build a thunk that forwards to `real_com`.
        ///
        /// Returns `None` when the page cannot be reserved, which is the only way this fails.
        pub(crate) fn build(real_com: usize) -> Option<Self> {
            let page = unsafe {
                VirtualAlloc(
                    core::ptr::null_mut(),
                    plan::TOTAL_BYTES,
                    MEM_COMMIT | MEM_RESERVE,
                    PAGE_EXECUTE_READWRITE,
                )
            }
            .cast::<u8>();
            if page.is_null() {
                return None;
            }
            // `MEM_COMMIT` pages arrive zeroed, which is exactly the `0x170` of zeroed object the
            // design asks for. Written out anyway: the guarantee is the platform's, the
            // requirement is ours, and one memset is cheaper than the reader having to know that.
            unsafe { core::ptr::write_bytes(page, 0, plan::TOTAL_BYTES) };

            let stubs_base = page as usize + plan::STUBS_AT;
            let stubs = stub_block();
            unsafe {
                core::ptr::copy_nonoverlapping(
                    stubs.as_ptr(),
                    page.add(plan::STUBS_AT),
                    stubs.len(),
                )
            };
            let vtable = vtable_for(stubs_base);
            unsafe {
                core::ptr::copy_nonoverlapping(
                    vtable.as_ptr(),
                    page.add(plan::VTABLE_AT).cast::<usize>(),
                    SLOT_COUNT,
                )
            };
            // The object: vptr at +0, the real Com at +0x170, zeroes in between.
            let object = unsafe { page.add(plan::OBJECT_AT) };
            unsafe {
                object
                    .cast::<usize>()
                    .write(page as usize + plan::VTABLE_AT);
                object.add(COM_SIZE).cast::<usize>().write(real_com);
            }
            Some(Self { page })
        }

        /// The address to write into `ChrCtrl+0x3b0`.
        pub(crate) fn object_address(&self) -> usize {
            self.page as usize + plan::OBJECT_AT
        }

        /// The real `ComManipulator` this thunk forwards to, read back out of the object.
        pub(crate) fn real_com(&self) -> usize {
            unsafe {
                self.page
                    .add(plan::OBJECT_AT + COM_SIZE)
                    .cast::<usize>()
                    .read()
            }
        }
    }

    impl Drop for Thunk {
        fn drop(&mut self) {
            // Nothing checks the result: a failing `VirtualFree` on a page we allocated means the
            // address is already gone, and there is no recovery to attempt from a Drop.
            unsafe { VirtualFree(self.page.cast(), 0, MEM_RELEASE) };
        }
    }

    /// The object must mirror the real `ComManipulator` plus room for our back-pointer.
    const _: () = assert!(OBJECT_BYTES == COM_SIZE + 8);
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `[vt+0x10]` -- `GetManipulatorType`. Not a production constant, because production does
    /// nothing special with it: the tick dispatcher calls it every frame and requires a nonzero
    /// answer, and other sites compare it against 5 (`Com`) and 6 (`Ride`), all of which the
    /// ORDINARY forwarding rule already delivers with no constant of ours to get wrong. It is
    /// named here so the test below can state that as a property rather than leaving it to luck.
    const SLOT_MANIPULATOR_TYPE: usize = 0x10;

    /// THE SIZE THAT IS A CRASH IF IT IS WRONG. Three oracles say 55 slots / `0x1b8` bytes, and
    /// the engine reads past a shorter table.
    #[test]
    fn the_vtable_is_fifty_five_slots_and_exactly_0x1b8_bytes() {
        assert_eq!(SLOT_COUNT, 55);
        assert_eq!(VTABLE_BYTES, 0x1b8);
        assert_eq!(SLOT_COUNT * core::mem::size_of::<usize>(), VTABLE_BYTES);
        assert_eq!(vtable_for(0x1000).len(), SLOT_COUNT);
    }

    /// Every slot gets a stub, at a predictable stride, and the block is exactly that long.
    #[test]
    fn the_stub_block_holds_one_padded_stub_per_slot() {
        let block = stub_block();
        assert_eq!(block.len(), SLOT_COUNT * STUB_STRIDE);
        for index in 0..SLOT_COUNT {
            let at = index * STUB_STRIDE;
            assert_eq!(
                &block[at..at + STUB_STRIDE],
                &stub_bytes(index * 8)[..],
                "slot {index} ({:#x})",
                index * 8
            );
        }
    }

    /// THE SLOT THAT WOULD FREE THE CREATURE'S REAL MANIPULATOR. It returns `this` and touches
    /// nothing -- in particular it must not contain the forwarding tail-jump.
    #[test]
    fn the_destructor_slot_returns_this_and_does_not_forward() {
        assert_eq!(slot_kind(SLOT_DESTRUCTOR), SlotKind::ReturnThis);
        let stub = stub_bytes(SLOT_DESTRUCTOR);
        // mov rax, rcx ; ret
        assert_eq!(&stub[..4], &[0x48, 0x8b, 0xc1, 0xc3]);
        assert!(
            !stub.windows(2).any(|w| w == [0xff, 0xa0]),
            "no jmp [rax+N]"
        );
        assert!(stub[4..].iter().all(|&b| b == INT3));
    }

    /// THE SLOT THE WHOLE FEATURE IS. `UpdateAi` returns without calling the creature's brain.
    #[test]
    fn the_update_ai_slot_is_a_no_op() {
        assert_eq!(slot_kind(SLOT_UPDATE_AI), SlotKind::NoOp);
        let stub = stub_bytes(SLOT_UPDATE_AI);
        // xor eax, eax ; ret
        assert_eq!(&stub[..3], &[0x31, 0xc0, 0xc3]);
        assert!(
            !stub.windows(2).any(|w| w == [0xff, 0xa0]),
            "no jmp [rax+N]"
        );
    }

    /// The other 53 forward, and each targets ITS OWN offset -- a stub that jumped through the
    /// wrong slot would run a plausible-looking wrong function.
    #[test]
    fn every_other_slot_forwards_through_its_own_offset() {
        let mut forwarded = 0;
        for index in 0..SLOT_COUNT {
            let offset = index * 8;
            if offset == SLOT_DESTRUCTOR || offset == SLOT_UPDATE_AI {
                continue;
            }
            forwarded += 1;
            let stub = stub_bytes(offset);
            // mov rcx, [rcx + BACK_POINTER_OFFSET]
            assert_eq!(&stub[..3], &[0x48, 0x8b, 0x89], "slot {offset:#x} prologue");
            assert_eq!(
                u32::from_le_bytes(stub[3..7].try_into().unwrap()),
                BACK_POINTER_OFFSET as u32,
                "slot {offset:#x} reads the back-pointer"
            );
            // mov rax, [rcx]
            assert_eq!(&stub[7..10], &[0x48, 0x8b, 0x01], "slot {offset:#x} vptr");
            // jmp qword ptr [rax + offset]
            assert_eq!(&stub[10..12], &[0xff, 0xa0], "slot {offset:#x} tail jump");
            assert_eq!(
                u32::from_le_bytes(stub[12..16].try_into().unwrap()),
                offset as u32,
                "slot {offset:#x} jumps through its OWN slot"
            );
        }
        assert_eq!(forwarded, 53, "55 slots, two of which do not forward");
    }

    /// `GetManipulatorType` is called every frame and must answer 5 (`Com`). Forwarding is what
    /// delivers that, so it must not have been swept into the no-op list.
    #[test]
    fn get_manipulator_type_forwards_so_the_answer_is_the_real_one() {
        assert_eq!(slot_kind(SLOT_MANIPULATOR_TYPE), SlotKind::Forward);
    }

    /// The back-pointer must live past everything the engine believes the object contains --
    /// including the `mov 0xb0(%rdi),%edx` direct read the tick dispatcher makes.
    #[test]
    fn the_back_pointer_sits_outside_the_mirrored_com_manipulator() {
        assert_eq!(BACK_POINTER_OFFSET, COM_SIZE);
        assert_eq!(BACK_POINTER_OFFSET, 0x170);
        const { assert!(BACK_POINTER_OFFSET > 0xb0, "past the direct field read") };
        assert_eq!(OBJECT_BYTES, 0x178);
    }

    /// The page layout must not overlap and must fit in a single 4 KiB page -- the object is
    /// written through, the vtable is read as `usize`, and the stubs are executed.
    #[test]
    fn the_page_plan_is_ordered_aligned_and_fits_one_page() {
        assert_eq!(plan::OBJECT_AT, 0);
        const { assert!(plan::VTABLE_AT >= plan::OBJECT_AT + OBJECT_BYTES) };
        assert_eq!(plan::VTABLE_AT % 16, 0, "vtable is pointer-aligned");
        assert_eq!(plan::STUBS_AT, plan::VTABLE_AT + VTABLE_BYTES);
        assert_eq!(plan::TOTAL_BYTES, plan::STUBS_AT + SLOT_COUNT * STUB_STRIDE);
        const { assert!(plan::TOTAL_BYTES <= 0x1000, "one page") };
    }

    /// A vtable built at a base has each entry pointing at its own stub, which is the arithmetic
    /// the `VirtualAlloc` path performs and the only part of it that can be checked on the host.
    #[test]
    fn the_vtable_entries_point_at_their_own_stubs() {
        let base = 0x1_0000_0000_usize;
        let vtable = vtable_for(base);
        for (index, entry) in vtable.iter().enumerate() {
            assert_eq!(*entry, base + index * STUB_STRIDE);
        }
        // ...and no two slots share a stub.
        let mut sorted = vtable.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), SLOT_COUNT);
    }

    /// Padding is `int3`, not zero: `00 00` decodes as `add [rax],al` and would run on into the
    /// next slot's code, which is the quietest possible way to execute the wrong function.
    #[test]
    fn short_stubs_are_padded_with_breakpoints() {
        for offset in [SLOT_DESTRUCTOR, SLOT_UPDATE_AI] {
            let stub = stub_bytes(offset);
            assert_eq!(*stub.last().unwrap(), INT3, "slot {offset:#x}");
        }
    }
}
