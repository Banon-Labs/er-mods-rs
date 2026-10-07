//! Generates the prologue this DLL byte-checks, from named `iced-x86` instructions.
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

use iced_x86::code_asm::*;
use prologue_build::{Assemble, Image, PrologueSpec, Shape, generate};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// "A menu has the mouse", `bool(CSMenuManImp*)`, 1.17 `0x140766650`. Below the `0xafefe9`
/// boundary, so 1.17.0 and 1.17.1 have it at the same address with the same bytes.
const MENU_HAS_MOUSE_1170_VA: u64 = 0x140766650;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[(
            PrologueSpec {
                name: "MENU_HAS_MOUSE_1171_PROLOGUE",
                doc: "`mov [rsp+0x10],rbx; mov [rsp+0x18],rbp; mov [rsp+0x20],rsi; push rdi;\n\
                      sub rsp,0x20; cmp byte [rcx+0x1a],0` -- the opening of the 1.17\n\
                      menu-has-the-mouse predicate.",
                visibility: "pub(crate)",
                shape: Shape::Array,
                image: Image::EldenRing1170,
                va: MENU_HAS_MOUSE_1170_VA,
                take: 0,
                pin: &[
                    0x48, 0x89, 0x5c, 0x24, 0x10, 0x48, 0x89, 0x6c, 0x24, 0x18, 0x48, 0x89, 0x74,
                    0x24, 0x20, 0x57, 0x48, 0x83, 0xec, 0x20, 0x80, 0x79, 0x1a, 0x00,
                ],
            },
            (|asm| {
                asm.mov(qword_ptr(rsp + 0x10), rbx)?;
                asm.mov(qword_ptr(rsp + 0x18), rbp)?;
                asm.mov(qword_ptr(rsp + 0x20), rsi)?;
                asm.push(rdi)?;
                asm.sub(rsp, 0x20)?;
                asm.cmp(byte_ptr(rcx + 0x1a), 0)?;
                Ok(())
            }) as Assemble,
        )],
        "generated_prologues.rs",
    );
}
