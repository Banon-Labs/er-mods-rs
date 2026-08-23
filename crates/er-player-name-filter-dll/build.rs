//! Generates the three prologues this DLL byte-checks, from named `iced-x86` instructions.
//!
//! See `build-support/prologue_build.rs` for why these are generated rather than hand-typed and
//! for what verifies the result.

#[allow(dead_code)]
mod prologue_build {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../build-support/prologue_build.rs"
    ));
}

use iced_x86::Register;
use iced_x86::code_asm::*;
use prologue_build::{Assemble, Image, PrologueSpec, Shape, generate, mov_r64_rm64};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// `CS::SessionManagerPlayerEntryBase::Copy`.
const SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_VA: u64 = 0x1423f1bf0;
/// `CS::GetPlayerChrName(MenuString *out, PlayerIns *player, char decorate)`.
const GET_PLAYER_CHR_NAME_VA: u64 = 0x14075f800;
/// `CS::PlayerGameData::CopyChrName(PlayerGameData *pgd, const wchar_t *name)`.
const PLAYER_GAME_DATA_COPY_CHR_NAME_VA: u64 = 0x1402610c0;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[
            (
                PrologueSpec {
                    name: "SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_PROLOGUE",
                    doc: "`mov [rsp+8],rbx; mov [rsp+0x10],rbp; mov [rsp+0x18],rsi` -- the three\n\
                          register spills `CS::SessionManagerPlayerEntryBase::Copy` opens with.",
                    visibility: "",
                    shape: Shape::Array,
                    image: Image::EldenRing,
                    va: SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x6c, 0x24, 0x10, 0x48, 0x89,
                        0x74, 0x24, 0x18,
                    ],
                },
                (|asm| {
                    asm.mov(qword_ptr(rsp + 0x08), rbx)?;
                    asm.mov(qword_ptr(rsp + 0x10), rbp)?;
                    asm.mov(qword_ptr(rsp + 0x18), rsi)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "GET_PLAYER_CHR_NAME_PROLOGUE",
                    doc: "`mov rax, rsp; push rbp; push rdi; push r14`.\n\
                          \n\
                          `mov rax, rsp` has two legal encodings and the game ships the rm64 one,\n\
                          `48 8b c4`. `CodeAssembler::mov(rax, rsp)` would pick the other,\n\
                          `48 89 e0`, and a prologue that differs by one byte is a hook that\n\
                          byte-checks itself off on every launch -- so the exact opcode is named\n\
                          through [`mov_r64_rm64`] rather than left to the assembler.",
                    visibility: "",
                    shape: Shape::Array,
                    image: Image::EldenRing,
                    va: GET_PLAYER_CHR_NAME_VA,
                    take: 0,
                    pin: &[0x48, 0x8b, 0xc4, 0x55, 0x57, 0x41, 0x56],
                },
                (|asm| {
                    mov_r64_rm64(asm, Register::RAX, Register::RSP)?;
                    asm.push(rbp)?;
                    asm.push(rdi)?;
                    asm.push(r14)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "PLAYER_GAME_DATA_COPY_CHR_NAME_PROLOGUE",
                    doc: "`mov [rsp+8],rbx; push rdi; sub rsp,0x20`.",
                    visibility: "",
                    shape: Shape::Array,
                    image: Image::EldenRing,
                    va: PLAYER_GAME_DATA_COPY_CHR_NAME_VA,
                    take: 0,
                    pin: &[0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x20],
                },
                (|asm| {
                    asm.mov(qword_ptr(rsp + 0x08), rbx)?;
                    asm.push(rdi)?;
                    asm.sub(rsp, 0x20)?;
                    Ok(())
                }) as Assemble,
            ),
        ],
        "generated_prologues.rs",
    );
}
