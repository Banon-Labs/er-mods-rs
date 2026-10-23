//! Generates the windows the `LockCamParam` getter is checked against, from named `iced-x86`
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

use prologue_build::{Assemble, Image, PrologueSpec, Shape, generate, xor_r32_rm32};

const SUPPORT: &str = "../../build-support/prologue_build.rs";
/// The one place this crate's getter address is written down; see its own docs.
const SITES: &str = "getter_sites.rs";

include!(concat!(env!("CARGO_MANIFEST_DIR"), "/getter_sites.rs"));

// Each virtual address is written as `GAME_IMAGE_BASE + (rva + offset) as u64` rather than through
// a helper, because `scripts/rva_symbols.py` evaluates arithmetic and not calls.
const LOCK_CAM_FRAME_VA: u64 =
    GAME_IMAGE_BASE + (LOOKUP_LOCK_CAM_PARAM_RVA + LOCK_CAM_FRAME_WINDOW_OFFSET) as u64;
const LOCK_CAM_TABLE_VA: u64 =
    GAME_IMAGE_BASE + (LOOKUP_LOCK_CAM_PARAM_RVA + LOCK_CAM_TABLE_WINDOW_OFFSET) as u64;
const LOCK_CAM_OUT_STORE_VA: u64 =
    GAME_IMAGE_BASE + (LOOKUP_LOCK_CAM_PARAM_RVA + LOCK_CAM_OUT_STORE_WINDOW_OFFSET) as u64;

/// `mov qword [rsp+0x20],-2`, the frame marker the getter stores third.
const FRAME_MARKER_SLOT: i32 = 0x20;
const FRAME_MARKER: i32 = -2;
/// `GetParamResCap`'s table index for `LockCamParam`.
const LOCK_CAM_PARAM_TABLE: i32 = 0x1f;
/// The id slot's displacement in `mov [rsi+8],edi`, from the same constant the runtime reads.
const OUT_ID_DISPLACEMENT: i32 = LOCK_CAM_OUT_ID_SLOT as i32;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    prologue_build::declare_rerun(SITES);
    generate(
        &[
            (
                PrologueSpec {
                    name: "LOCK_CAM_FRAME_WINDOW",
                    doc: "2.7.2.0 `LookupLockCamParam+0x6`: `MOV qword [RSP+0x20],-2`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: LOCK_CAM_FRAME_VA,
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
                    name: "LOCK_CAM_TABLE_WINDOW",
                    doc: "2.7.2.0 `LookupLockCamParam+0x67`: `XOR R8D,R8D; LEA EDX,[R8+0x1f]`, the\n\
                          `LockCamParam` table index handed to `GetParamResCap`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: LOCK_CAM_TABLE_VA,
                    take: 0,
                    pin: &[0x45, 0x33, 0xc0, 0x41, 0x8d, 0x50, 0x1f],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    // The `33` (r32 <- rm32) direction the game ships; the assembler picks `31`.
                    xor_r32_rm32(asm, iced_x86::Register::R8D, iced_x86::Register::R8D)?;
                    asm.lea(edx, ptr(r8 + LOCK_CAM_PARAM_TABLE))?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "LOCK_CAM_OUT_STORE_WINDOW",
                    doc: "2.7.2.0 `LookupLockCamParam+0x145`: `MOV [RSI+8],EDI; MOV [RSI],RBX`, the\n\
                          id into `out+8` and the row pointer into `out+0`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: LOCK_CAM_OUT_STORE_VA,
                    take: 0,
                    pin: &[0x89, 0x7e, 0x08, 0x48, 0x89, 0x1e],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(dword_ptr(rsi + OUT_ID_DISPLACEMENT), edi)?;
                    // `[rsi]` with no displacement byte: the row slot is `out+0`
                    // (`LOCK_CAM_OUT_ROW_SLOT`), and the game ships the `mod=00` form.
                    asm.mov(qword_ptr(rsi), rbx)?;
                    Ok(())
                }) as Assemble,
            ),
        ],
        "generated_getter_windows.rs",
    );
}
