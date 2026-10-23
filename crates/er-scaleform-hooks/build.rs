//! Generates the descriptor-ring mid hook's two checked windows from named `iced-x86` instructions.
//!
//! See `build-support/prologue_build.rs` for why these are generated rather than hand-typed, and
//! `src/descriptor_guard.rs` for what the windows are.

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

/// The Scaleform CBV_SRV_UAV descriptor-ring advance, `advance(ring, count)`, 2.7.2.0.
const DESCRIPTOR_ADVANCE_VA: u64 = 0x140ecca90;
/// Offsets into the advance, identical in 1.16.2 and 2.7.2.0 (the 0x18b-byte body differs only
/// in one rip-relative `lea` displacement at `+0x9d`).
const DESCRIPTOR_ADVANCE_PAGE_ROLL_LOAD: u64 = 0x8c;
const DESCRIPTOR_ADVANCE_EPILOGUE_JOIN: u64 = 0x157;
/// Each window's address, named so `verify-aob-patterns.py` can read the spec's `va:`.
const PAGE_ROLL_LOAD_VA: u64 = DESCRIPTOR_ADVANCE_VA + DESCRIPTOR_ADVANCE_PAGE_ROLL_LOAD;
const EPILOGUE_JOIN_VA: u64 = DESCRIPTOR_ADVANCE_VA + DESCRIPTOR_ADVANCE_EPILOGUE_JOIN;
/// `ring+0x38`, the current page.
const RING_CURRENT_PAGE: i64 = 0x38;
/// `[rsp+0x30]`, the out slot of the page's `GetDevice` call, released at the epilogue join.
const DEVICE_OUT_STACK_SLOT: i32 = 0x30;
/// `[rsp+0x48]` and `[rsp+0x38]`, where the slow path saved `RDI` and `RSI` at `+0x21` / `+0x28`.
const SAVED_RDI_STACK_SLOT: i64 = 0x48;
const SAVED_RSI_STACK_SLOT: i64 = 0x38;

/// `+0x8c`: the page-roll branch's first three instructions, which the 14-byte jump overwrites.
fn page_roll_load(asm: &mut iced_x86::code_asm::CodeAssembler) -> Result<(), iced_x86::IcedError> {
    use iced_x86::code_asm::*;
    mov_r64_mem(asm, Register::RAX, Register::RBX, RING_CURRENT_PAGE)?;
    asm.lea(r8, ptr(rsp + DEVICE_OUT_STACK_SLOT))?;
    asm.mov(qword_ptr(rsp + DEVICE_OUT_STACK_SLOT), rsi)?;
    Ok(())
}

/// `+0x157`: where both page-selection branches rejoin, release the device and restore `RDI`/`RSI`.
fn epilogue_join(asm: &mut iced_x86::code_asm::CodeAssembler) -> Result<(), iced_x86::IcedError> {
    use iced_x86::code_asm::*;
    mov_r64_mem(
        asm,
        Register::RCX,
        Register::RSP,
        i64::from(DEVICE_OUT_STACK_SLOT),
    )?;
    mov_r64_mem(asm, Register::RDI, Register::RSP, SAVED_RDI_STACK_SLOT)?;
    mov_r64_mem(asm, Register::RSI, Register::RSP, SAVED_RSI_STACK_SLOT)?;
    asm.test(rcx, rcx)?;
    Ok(())
}

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[
            (
                PrologueSpec {
                    name: "PAGE_ROLL_LOAD_WINDOW",
                    doc: "2.7.2.0 descriptor advance `+0x8c`, the page-roll branch's page load:\n\
                          `MOV RAX,[RBX+0x38]; LEA R8,[RSP+0x30]; MOV [RSP+0x30],RSI`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: PAGE_ROLL_LOAD_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x8b, 0x43, 0x38, 0x4c, 0x8d, 0x44, 0x24, 0x30, 0x48, 0x89, 0x74,
                        0x24, 0x30,
                    ],
                },
                page_roll_load as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "EPILOGUE_JOIN_WINDOW",
                    doc: "2.7.2.0 descriptor advance `+0x157`, the join both page-selection branches\n\
                          reach: `MOV RCX,[RSP+0x30]; MOV RDI,[RSP+0x48]; MOV RSI,[RSP+0x38];\n\
                          TEST RCX,RCX`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: EPILOGUE_JOIN_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x8b, 0x4c, 0x24, 0x30, 0x48, 0x8b, 0x7c, 0x24, 0x48, 0x48, 0x8b,
                        0x74, 0x24, 0x38, 0x48, 0x85, 0xc9,
                    ],
                },
                epilogue_join as prologue_build::Assemble,
            ),
        ],
        "generated_descriptor_advance_windows.rs",
    );
}
