//! Generates the prologue this DLL byte-checks before it detours Elden Ring's per-frame dynamic
//! resolution update, from named `iced-x86` instructions.
//!
//! See `build-support/prologue_build.rs` for why these are generated rather than hand-typed and for
//! what verifies the result. The spec names `Image::EldenRing1171`: the function is above the
//! `0xafefe9` boundary, so 1.17.1 moved it `0x70` while the stack cookie its third instruction
//! loads stayed put, and the displacement of that load differs from 1.17.0's. The redundant REX
//! prefix of its first instruction keeps the generator from masking that field, so these bytes are
//! right for 1.17.1 alone, which is the build the DLL refuses every other build for.

#[allow(dead_code)]
mod prologue_build {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../build-support/prologue_build.rs"
    ));
}

use iced_x86::code_asm::*;
use iced_x86::{Code, Instruction, Register};
use prologue_build::{
    Assemble, Image, PrologueSpec, Shape, generate, mov_rax_rip_absolute, rex_push,
};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// The per-frame dynamic resolution update, `void(DrsConfig*, bool)`, 1.17.1 `0x141b2e9b0`
/// (1.17.0 `0x141b2e940`, 1.16.2 `0x141b2cb40`). Its sixth instruction, `cmp byte [rcx],0`, is the
/// read of the controller's enable byte.
const DRS_UPDATE_1171_VA: u64 = 0x141b2e9b0;

/// The stack cookie global the prologue loads, `__security_cookie` (1.17.x `0x143c5ee10`).
const SECURITY_COOKIE_1171_VA: u64 = 0x143c5ee10;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[(
            PrologueSpec {
                name: "DRS_UPDATE_1171_PROLOGUE",
                doc: "`push rbx` (with MSVC's redundant REX); `sub rsp,0x40`; `mov rax,[rip+..]`\n\
                      (the stack cookie); `xor rax,rsp`; `mov [rsp+0x38],rax`; `cmp byte [rcx],0`\n\
                      -- the opening of the 1.17.1 dynamic resolution update.",
                visibility: "pub(crate)",
                shape: Shape::Array,
                image: Image::EldenRing1171,
                va: DRS_UPDATE_1171_VA,
                take: 0,
                pin: &[
                    0x40, 0x53, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0x05, 0x53, 0x04, 0x13, 0x02,
                    0x48, 0x33, 0xc4, 0x48, 0x89, 0x44, 0x24, 0x38, 0x80, 0x39, 0x00,
                ],
            },
            (|asm| {
                rex_push(asm, rbx)?;
                asm.sub(rsp, 0x40)?;
                mov_rax_rip_absolute(asm, SECURITY_COOKIE_1171_VA)?;
                // The `33` (r64 <- rm64) direction; `asm.xor(rax, rsp)` picks `31`.
                asm.add_instruction(Instruction::with2(
                    Code::Xor_r64_rm64,
                    Register::RAX,
                    Register::RSP,
                )?)?;
                asm.mov(qword_ptr(rsp + 0x38), rax)?;
                asm.cmp(byte_ptr(rcx), 0)?;
                Ok(())
            }) as Assemble,
        )],
        "generated_prologues.rs",
    );
}
