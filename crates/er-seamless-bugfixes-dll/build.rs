//! Generates the guarded functions' expected prologues from named `iced-x86` instructions.
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

/// `bool CS::SpecialEffect::HasSpecialEffectId(SpecialEffect *container, uint spEffectId)`.
const HAS_SPECIAL_EFFECT_ID_VA: u64 = 0x1404f9940;
/// The `container->head == null` early-out `HasSpecialEffectId` jumps to; the branch is part of
/// the checked prologue, so its destination has to be named too.
const HAS_SPECIAL_EFFECT_ID_EMPTY_RETURN_VA: u64 = 0x1404f995e;
/// `int CS::SpecialEffect::Apply(SpecialEffect *container, int spEffectId, ChrIns *, ChrIns *,
/// FloatVector4 *, byte, bool, byte)`.
const APPLY_VA: u64 = 0x1404fa8e0;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[
            (
                PrologueSpec {
                    name: "HAS_SPECIAL_EFFECT_ID_PROLOGUE",
                    doc: "1.16.2 prologue of `CS::SpecialEffect::HasSpecialEffectId`:\n\
                          `MOV RCX,[RCX+8]; TEST RCX,RCX; JZ +0x15`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: HAS_SPECIAL_EFFECT_ID_VA,
                    take: 0,
                    pin: &[0x48, 0x8b, 0x49, 0x08, 0x48, 0x85, 0xc9, 0x74, 0x15],
                },
                |asm| {
                    mov_r64_mem(asm, Register::RCX, Register::RCX, 8)?;
                    asm.test(iced_x86::code_asm::rcx, iced_x86::code_asm::rcx)?;
                    asm.jz(HAS_SPECIAL_EFFECT_ID_EMPTY_RETURN_VA)?;
                    Ok(())
                },
            ),
            (
                PrologueSpec {
                    name: "APPLY_PROLOGUE",
                    doc: "1.16.2 prologue of `CS::SpecialEffect::Apply`:\n\
                          `MOV [RSP+0x10],RBP; MOV [RSP+0x18],RSI`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: APPLY_VA,
                    take: 0,
                    pin: &[0x48, 0x89, 0x6c, 0x24, 0x10, 0x48, 0x89, 0x74, 0x24, 0x18],
                },
                |asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(qword_ptr(rsp + 0x10), rbp)?;
                    asm.mov(qword_ptr(rsp + 0x18), rsi)?;
                    Ok(())
                },
            ),
        ],
        "generated_null_special_effect_prologues.rs",
    );
}
