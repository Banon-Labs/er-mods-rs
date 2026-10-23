//! Generates the prologue this DLL byte-checks before it detours Elden Ring's per-frame dynamic
//! resolution update, from named `iced-x86` instructions.
//!
//! See `build-support/prologue_build.rs` for why these are generated rather than hand-typed and for
//! what verifies the result. The third instruction loads the stack cookie rip-relatively. The
//! generated `DRS_UPDATE_PROLOGUE_MASK` ignores that displacement (bytes `+9..+13`, which read
//! `73 1d 13 02` on 2.7.2.0), and `drs.rs` compares under the mask, so the runtime check holds
//! every other byte and does not depend on where the cookie sits. The pin still records the
//! 2.7.2.0 encoding in full.

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

/// The per-frame dynamic resolution update, `void(DrsConfig*, bool)`, 2.7.2.0 `0x141b300a0`
/// (1.17.1 `0x141b2e9b0`, 1.16.2 `0x141b2cb40`). Its sixth instruction, `cmp byte [rcx],0`, is the
/// read of the controller's enable byte.
const DRS_UPDATE_VA: u64 = 0x141b300a0;

/// The stack cookie global the prologue loads, `__security_cookie` (2.7.2.0 `0x143c61e20`).
const SECURITY_COOKIE_VA: u64 = 0x143c61e20;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[(
            PrologueSpec {
                name: "DRS_UPDATE_PROLOGUE",
                doc: "`push rbx` (with MSVC's redundant REX); `sub rsp,0x40`; `mov rax,[rip+..]`\n\
                      (the stack cookie); `xor rax,rsp`; `mov [rsp+0x38],rax`; `cmp byte [rcx],0`\n\
                      -- the opening of the 2.7.2.0 dynamic resolution update.",
                visibility: "pub(crate)",
                shape: Shape::Array,
                image: Image::EldenRing2720,
                va: DRS_UPDATE_VA,
                take: 0,
                pin: &[
                    0x40, 0x53, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0x05, 0x73, 0x1d, 0x13, 0x02,
                    0x48, 0x33, 0xc4, 0x48, 0x89, 0x44, 0x24, 0x38, 0x80, 0x39, 0x00,
                ],
            },
            (|asm| {
                rex_push(asm, rbx)?;
                asm.sub(rsp, 0x40)?;
                mov_rax_rip_absolute(asm, SECURITY_COOKIE_VA)?;
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
