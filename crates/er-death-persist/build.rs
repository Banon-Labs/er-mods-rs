//! Generates the windows each hooked getter is checked against, from named `iced-x86`
//! instructions.
//!
//! See `build-support/prologue_build.rs` for why these are generated rather than hand-typed: a
//! window is what stands between a detour and the middle of some other function, so the bytes it
//! compares against must not be a number somebody copied.

#[allow(dead_code)]
mod prologue_build {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../build-support/prologue_build.rs"
    ));
}

use iced_x86::{Code, Instruction, Register};
use prologue_build::{Assemble, Image, PrologueSpec, Shape, generate};

const SUPPORT: &str = "../../build-support/prologue_build.rs";
/// The one place this crate's addresses are written down; see its own docs.
const SITES: &str = "getter_sites.rs";

include!(concat!(env!("CARGO_MANIFEST_DIR"), "/getter_sites.rs"));

// Each virtual address is written as `GAME_IMAGE_BASE + (rva + offset) as u64` rather than through
// a helper, because `scripts/rva_symbols.py` evaluates arithmetic and not calls.
const SP_EFFECT_FRAME_VA: u64 =
    GAME_IMAGE_BASE + (GET_SP_EFFECT_PARAM_1171_RVA + GETTER_FRAME_WINDOW_OFFSET) as u64;
const SP_EFFECT_TABLE_VA: u64 =
    GAME_IMAGE_BASE + (GET_SP_EFFECT_PARAM_1171_RVA + SP_EFFECT_TABLE_WINDOW_OFFSET) as u64;
const SP_EFFECT_ROW_STORE_VA: u64 =
    GAME_IMAGE_BASE + (GET_SP_EFFECT_PARAM_1171_RVA + SP_EFFECT_ROW_STORE_WINDOW_OFFSET) as u64;
const SP_EFFECT_VFX_FRAME_VA: u64 =
    GAME_IMAGE_BASE + (GET_SP_EFFECT_VFX_PARAM_1171_RVA + GETTER_FRAME_WINDOW_OFFSET) as u64;
const SP_EFFECT_VFX_TABLE_VA: u64 =
    GAME_IMAGE_BASE + (GET_SP_EFFECT_VFX_PARAM_1171_RVA + SP_EFFECT_VFX_TABLE_WINDOW_OFFSET) as u64;
const SP_EFFECT_VFX_HIT_STORE_VA: u64 = GAME_IMAGE_BASE
    + (GET_SP_EFFECT_VFX_PARAM_1171_RVA + SP_EFFECT_VFX_HIT_STORE_WINDOW_OFFSET) as u64;
const SP_EFFECT_VFX_MISS_STORE_VA: u64 = GAME_IMAGE_BASE
    + (GET_SP_EFFECT_VFX_PARAM_1171_RVA + SP_EFFECT_VFX_MISS_STORE_WINDOW_OFFSET) as u64;

/// `mov qword [rsp+0x20],-2`, the frame marker both getters store third.
const FRAME_MARKER_SLOT: i32 = 0x20;
const FRAME_MARKER: i32 = -2;
/// `GetParamResCap`'s table index for each getter, and the out-struct offset of the row pointer
/// the SpEffectVfxParam getter writes.
const SP_EFFECT_PARAM_TABLE: i32 = 0xf;
const SP_EFFECT_VFX_PARAM_TABLE: i32 = 0x10;
const VFX_OUT_ROW: i32 = 0x8;

/// `xor r8d,r8d` in the `33` (r32 <- rm32) direction the game ships; the assembler picks `31`.
fn xor_r8d_r8d(asm: &mut iced_x86::code_asm::CodeAssembler) -> Result<(), iced_x86::IcedError> {
    asm.add_instruction(Instruction::with2(
        Code::Xor_r32_rm32,
        Register::R8D,
        Register::R8D,
    )?)
}

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    prologue_build::declare_rerun(SITES);
    generate(
        &[
            (
                PrologueSpec {
                    name: "SP_EFFECT_FRAME_WINDOW",
                    doc: "1.17.1 `GetSpEffectParam+0x6`: `MOV qword [RSP+0x20],-2`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing1171,
                    va: SP_EFFECT_FRAME_VA,
                    take: 0,
                    pin: &[0x48, 0xc7, 0x44, 0x24, 0x20, 0xfe, 0xff, 0xff, 0xff],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(qword_ptr(rsp + FRAME_MARKER_SLOT), FRAME_MARKER)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SP_EFFECT_TABLE_WINDOW",
                    doc: "1.17.1 `GetSpEffectParam+0x6f`: `XOR R8D,R8D; LEA EDX,[R8+0xf]`, the\n\
                          SpEffectParam table index handed to `GetParamResCap`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing1171,
                    va: SP_EFFECT_TABLE_VA,
                    take: 0,
                    pin: &[0x45, 0x33, 0xc0, 0x41, 0x8d, 0x50, 0x0f],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    xor_r8d_r8d(asm)?;
                    asm.lea(edx, ptr(r8 + SP_EFFECT_PARAM_TABLE))?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SP_EFFECT_ROW_STORE_WINDOW",
                    doc: "1.17.1 `GetSpEffectParam+0x15d`: `MOV [R14],RBX`, the row pointer into\n\
                          `out+0`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing1171,
                    va: SP_EFFECT_ROW_STORE_VA,
                    take: 0,
                    pin: &[0x49, 0x89, 0x1e],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(qword_ptr(r14), rbx)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SP_EFFECT_VFX_FRAME_WINDOW",
                    doc: "1.17.1 SpEffectVfxParam getter `+0x6`: `MOV qword [RSP+0x20],-2`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing1171,
                    va: SP_EFFECT_VFX_FRAME_VA,
                    take: 0,
                    pin: &[0x48, 0xc7, 0x44, 0x24, 0x20, 0xfe, 0xff, 0xff, 0xff],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(qword_ptr(rsp + FRAME_MARKER_SLOT), FRAME_MARKER)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SP_EFFECT_VFX_TABLE_WINDOW",
                    doc: "1.17.1 SpEffectVfxParam getter `+0x67`: `XOR R8D,R8D; LEA EDX,[R8+0x10]`,\n\
                          the SpEffectVfxParam table index handed to `GetParamResCap`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing1171,
                    va: SP_EFFECT_VFX_TABLE_VA,
                    take: 0,
                    pin: &[0x45, 0x33, 0xc0, 0x41, 0x8d, 0x50, 0x10],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    xor_r8d_r8d(asm)?;
                    asm.lea(edx, ptr(r8 + SP_EFFECT_VFX_PARAM_TABLE))?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SP_EFFECT_VFX_HIT_STORE_WINDOW",
                    doc: "1.17.1 SpEffectVfxParam getter `+0xbd`: `MOV [RBX+8],RAX`, the row pointer\n\
                          into `out+8` on a hit.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing1171,
                    va: SP_EFFECT_VFX_HIT_STORE_VA,
                    take: 0,
                    pin: &[0x48, 0x89, 0x43, 0x08],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(qword_ptr(rbx + VFX_OUT_ROW), rax)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SP_EFFECT_VFX_MISS_STORE_WINDOW",
                    doc: "1.17.1 SpEffectVfxParam getter `+0xc5`: `MOV [RBX+8],RBP`, the same slot\n\
                          on a miss.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing1171,
                    va: SP_EFFECT_VFX_MISS_STORE_VA,
                    take: 0,
                    pin: &[0x48, 0x89, 0x6b, 0x08],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(qword_ptr(rbx + VFX_OUT_ROW), rbp)?;
                    Ok(())
                }) as Assemble,
            ),
        ],
        "generated_getter_windows.rs",
    );
}
