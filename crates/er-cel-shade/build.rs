//! Generates the prologues this DLL byte-checks before it calls or hooks the game for the Sky tab,
//! from named `iced-x86` instructions. The "a menu has the mouse" predicate the menu hooks to free
//! the cursor is `er-overlay-input`'s, and its prologue is generated in that crate's `build.rs`.
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

/// `WorldAreaTime::RequestTime`, 1.17 `0x1406442a0`.
const REQUEST_TIME_1170_VA: u64 = 0x1406442a0;
/// `WorldAreaWeather::ChangeWeather`, 1.17 `0x140647940`.
const CHANGE_WEATHER_1170_VA: u64 = 0x140647940;
/// `GXParamDB::Update(this, f32 time, u32)`, 1.17.0 `0x141a4e730`. Above the `0xafefe9` boundary,
/// so 1.17.1 holds the same bytes `0x70` higher, at `0x141a4e7a0`.
const PARAM_DB_UPDATE_1170_VA: u64 = 0x141a4e730;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    // The two calls the Sky tab makes (`src/sky/game.rs`), checked before either is made. Both sit
    // below the `0xafefe9` boundary, so 1.17.0 and 1.17.1 hold them at the same address.
    generate(
        &[
            (
                PrologueSpec {
                    name: "REQUEST_TIME_1171_PROLOGUE",
                    doc: "`mov [rsp+0x10],rbx; mov [rsp+0x18],rbp; mov [rsp+0x20],rsi; push rdi;\n\
                          sub rsp,0x60; cmp byte [rcx+0x114],0` -- the opening of\n\
                          `WorldAreaTime::RequestTime(this, hour, minute, second)`, whose first\n\
                          test is the time-set lock at +0x114.",
                    visibility: "pub(crate)",
                    shape: Shape::Array,
                    image: Image::EldenRing1170,
                    va: REQUEST_TIME_1170_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x89, 0x5c, 0x24, 0x10, 0x48, 0x89, 0x6c, 0x24, 0x18, 0x48, 0x89,
                        0x74, 0x24, 0x20, 0x57, 0x48, 0x83, 0xec, 0x60, 0x80, 0xb9, 0x14, 0x01,
                        0x00, 0x00, 0x00,
                    ],
                },
                (|asm| {
                    asm.mov(qword_ptr(rsp + 0x10), rbx)?;
                    asm.mov(qword_ptr(rsp + 0x18), rbp)?;
                    asm.mov(qword_ptr(rsp + 0x20), rsi)?;
                    asm.push(rdi)?;
                    asm.sub(rsp, 0x60)?;
                    asm.cmp(byte_ptr(rcx + 0x114), 0)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "CHANGE_WEATHER_1171_PROLOGUE",
                    doc: "`movss [rcx+0x8c],xmm2; mov [rcx+0x88],dx; mov [rcx+0x90],r9b;\n\
                          mov byte [rcx+0x91],1` -- all of\n\
                          `WorldAreaWeather::ChangeWeather(this, type, lifespan, immediate)` but\n\
                          its `ret`: it latches the request the weather's own update applies.",
                    visibility: "pub(crate)",
                    shape: Shape::Array,
                    image: Image::EldenRing1170,
                    va: CHANGE_WEATHER_1170_VA,
                    take: 0,
                    pin: &[
                        0xf3, 0x0f, 0x11, 0x91, 0x8c, 0x00, 0x00, 0x00, 0x66, 0x89, 0x91, 0x88,
                        0x00, 0x00, 0x00, 0x44, 0x88, 0x89, 0x90, 0x00, 0x00, 0x00, 0xc6, 0x81,
                        0x91, 0x00, 0x00, 0x00, 0x01,
                    ],
                },
                (|asm| {
                    asm.movss(dword_ptr(rcx + 0x8c), xmm2)?;
                    asm.mov(word_ptr(rcx + 0x88), dx)?;
                    asm.mov(byte_ptr(rcx + 0x90), r9b)?;
                    asm.mov(byte_ptr(rcx + 0x91), 1)?;
                    Ok(())
                }) as Assemble,
            ),
        ],
        "generated_sky_prologues.rs",
    );
    // The Sun section's detour (`src/sky/light.rs`): the function that blends every gparam field
    // for the time of day and the weather, once a frame.
    generate(
        &[(
            PrologueSpec {
                name: "PARAM_DB_UPDATE_1171_PROLOGUE",
                doc: "`mov rax,rsp; mov [rax+0x18],r8d; movss [rax+0x10],xmm1; push rbp; push\n\
                          rsi; push rdi; push r12; push r13; push r14; push r15; lea rbp,[rax-0x5f];\n\
                          sub rsp,0xb0` -- the opening of `GXParamDB::Update(this, time, n)`, whose\n\
                          second argument is a float in xmm1.",
                visibility: "pub(crate)",
                shape: Shape::Array,
                image: Image::EldenRing1170,
                va: PARAM_DB_UPDATE_1170_VA,
                take: 0,
                pin: &[
                    0x48, 0x8b, 0xc4, 0x44, 0x89, 0x40, 0x18, 0xf3, 0x0f, 0x11, 0x48, 0x10, 0x55,
                    0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57, 0x48, 0x8d, 0x68,
                    0xa1, 0x48, 0x81, 0xec, 0xb0, 0x00, 0x00, 0x00,
                ],
            },
            (|asm| {
                // The game ships the rm64 encoding, `48 8b c4`, which `asm.mov(rax, rsp)`
                // would not pick.
                mov_r64_rm64(asm, Register::RAX, Register::RSP)?;
                asm.mov(dword_ptr(rax + 0x18), r8d)?;
                asm.movss(dword_ptr(rax + 0x10), xmm1)?;
                asm.push(rbp)?;
                asm.push(rsi)?;
                asm.push(rdi)?;
                asm.push(r12)?;
                asm.push(r13)?;
                asm.push(r14)?;
                asm.push(r15)?;
                asm.lea(rbp, ptr(rax - 0x5f))?;
                asm.sub(rsp, 0xb0)?;
                Ok(())
            }) as Assemble,
        )],
        "generated_light_prologues.rs",
    );
}
