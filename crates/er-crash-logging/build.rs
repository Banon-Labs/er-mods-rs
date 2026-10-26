//! Generates the vanilla-crash patch windows from named `iced-x86` instructions.
//!
//! See `build-support/prologue_build.rs` for why these are generated rather than hand-typed.

#[allow(dead_code)]
mod prologue_build {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../build-support/prologue_build.rs"
    ));
}

use iced_x86::Register;
use prologue_build::{Image, PrologueSpec, Shape, generate, mov_r64_mem};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// The leak assertion inside `CS::CSFreeListMemorySystem`'s shutdown, at the `CMP` that decides
/// whether to break. A 2.7.2.0 VA in `eldenring-deobf-1.17.2.bin`. See
/// `patches::freelist_shutdown_assert`.
const FREELIST_SHUTDOWN_ASSERT_VA: u64 = 0x140c59270;
/// Where the assertion's `JZ` lands: one byte past the `INT3`, which is also where the `INT3`
/// itself falls through to. Naming it is the whole argument for the patch, so it is named here
/// rather than encoded as a displacement.
const FREELIST_SHUTDOWN_ASSERT_RESUME_VA: u64 = 0x140c59277;
/// Offset within the node of the "this thread-local free-list is still checked out" flag the
/// assertion tests. Set by the registrar at `0x140c595f0`.
const FREE_LIST_NODE_IN_USE_FLAG: i64 = 0x18;
/// Offset of the allocator the shutdown loop calls `Free` through, loaded by the instruction
/// immediately after the `INT3` -- the one that pins the far side of the window.
const FREE_LIST_SYSTEM_ALLOCATOR: i64 = 8;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[(
            PrologueSpec {
                name: "FREELIST_SHUTDOWN_ASSERT_WINDOW",
                doc: "2.7.2.0 bytes of the leak assertion in `CS::CSFreeListMemorySystem`'s\n\
                      shutdown: `CMP byte [RDX+0x18],0; JZ +1; INT3; MOV RCX,[RDI+8]`.\n\
                      The `JZ` and the `MOV` are in the window on purpose -- they are what prove\n\
                      the `INT3` is advisory, and they pin an address a bare `0xcc` could not.",
                visibility: "pub(crate)",
                shape: Shape::Slice,
                image: Image::EldenRing2720,
                va: FREELIST_SHUTDOWN_ASSERT_VA,
                take: 0,
                pin: &[
                    0x80, 0x7a, 0x18, 0x00, 0x74, 0x01, 0xcc, 0x48, 0x8b, 0x4f, 0x08,
                ],
            },
            (|asm| {
                use iced_x86::code_asm::*;
                asm.cmp(byte_ptr(rdx + FREE_LIST_NODE_IN_USE_FLAG), 0)?;
                asm.jz(FREELIST_SHUTDOWN_ASSERT_RESUME_VA)?;
                asm.int3()?;
                mov_r64_mem(
                    asm,
                    Register::RCX,
                    Register::RDI,
                    FREE_LIST_SYSTEM_ALLOCATOR,
                )?;
                Ok(())
            }) as prologue_build::Assemble,
        )],
        "generated_freelist_shutdown_assert.rs",
    );
}
