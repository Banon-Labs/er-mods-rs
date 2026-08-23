//! Generates every prologue this shell byte-checks, from named `iced-x86` instructions.
//!
//! Two modules are covered: Elden Ring itself (`announce.rs`, `place_name.rs`) and Seamless
//! Co-op's `ersc.dll` (`local_invasion_filter.rs`). See `build-support/prologue_build.rs`.

#[allow(dead_code)]
mod prologue_build {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../build-support/prologue_build.rs"
    ));
}

use iced_x86::Register;
use iced_x86::code_asm::*;
use prologue_build::{Image, PrologueSpec, Shape, generate, mov_r64_mem, rex_push};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// `CS::FeSystemAnnounceView::Update`.
const ANNOUNCE_UPDATE_VA: u64 = 0x1408c47c0;
/// How much of `Update`'s opening the check reads. It deliberately stops PART WAY through the
/// `movaps` that spills `xmm6`, which is why the assembled sequence is longer than the constant.
const ANNOUNCE_UPDATE_CHECKED_BYTES: usize = 8;
/// `FUN_140d10b60(MsgRepositoryImp*, id) -> wchar_t*`, the `PlaceName` getter.
const PLACE_NAME_LOOKUP_VA: u64 = 0x140d10b60;

/// ERSC entry points, at Seamless Co-op v1.9.9's preferred base `0x180000000`.
const ERSC_SHOW_VA: u64 = 0x180022d30;
const ERSC_INVADE_ACTION_VA: u64 = 0x1800243e0;
const ERSC_CANCEL_ACTION_VA: u64 = 0x180024460;
const ERSC_BUILD_LOBBY_KEY_VA: u64 = 0x1800abc20;
/// `OSM+0x58`, the session object both option actions load first.
const ERSC_NEXT_OBJECT_OFFSET: i64 = 0x58;

/// The eight callee-saved pushes both `show` and `BuildLobbyKey` open with.
fn ersc_callee_saved_pushes(asm: &mut CodeAssembler) -> Result<(), iced_x86::IcedError> {
    asm.push(rbp)?;
    asm.push(r15)?;
    asm.push(r14)?;
    asm.push(r13)?;
    asm.push(r12)?;
    asm.push(rsi)?;
    asm.push(rdi)?;
    asm.push(rbx)
}

/// The shared opening of the two ERSC option actions: CET landing pad, two pushes, shadow space,
/// then the session load. They are byte-identical, which is itself part of the discriminator.
fn ersc_option_action_prologue(asm: &mut CodeAssembler) -> Result<(), iced_x86::IcedError> {
    asm.endbr64()?;
    asm.push(rsi)?;
    asm.push(rdi)?;
    asm.sub(rsp, 0x28)?;
    mov_r64_mem(asm, Register::RDI, Register::RCX, ERSC_NEXT_OBJECT_OFFSET)
}

const ERSC_OPTION_ACTION_PIN: &[u8] = &[
    0xf3, 0x0f, 0x1e, 0xfa, 0x56, 0x57, 0x48, 0x83, 0xec, 0x28, 0x48, 0x8b, 0x79, 0x58,
];

fn main() {
    prologue_build::declare_rerun(SUPPORT);

    generate(
        &[(
            PrologueSpec {
                name: "UPDATE_PROLOGUE",
                doc: "Opening bytes of `CS::FeSystemAnnounceView::Update`, so a game update that\n\
                      moves it fails closed instead of jumping mid-instruction. The check stops\n\
                      inside the `movaps` that spills `xmm6`, so this is the first\n\
                      `ANNOUNCE_UPDATE_CHECKED_BYTES` of `push rbx; sub rsp,0x30; movaps\n\
                      [rsp+0x20],xmm6`.",
                visibility: "pub",
                shape: Shape::Slice,
                image: Image::EldenRing,
                va: ANNOUNCE_UPDATE_VA,
                take: ANNOUNCE_UPDATE_CHECKED_BYTES,
                pin: &[0x40, 0x53, 0x48, 0x83, 0xec, 0x30, 0x0f, 0x29],
            },
            (|asm| {
                rex_push(asm, rbx)?;
                asm.sub(rsp, 0x30)?;
                asm.movaps(xmmword_ptr(rsp + 0x20), xmm6)?;
                Ok(())
            }) as prologue_build::Assemble,
        )],
        "generated_announce_prologues.rs",
    );

    generate(
        &[(
            PrologueSpec {
                name: "PLACE_NAME_LOOKUP_PROLOGUE",
                doc: "Opening bytes of the `PlaceName` getter, so a game update that moves it\n\
                      fails closed instead of calling into the middle of something else:\n\
                      `mov [rsp+8],rbx; push rdi; sub rsp,0x20`.",
                visibility: "pub",
                shape: Shape::Slice,
                image: Image::EldenRing,
                va: PLACE_NAME_LOOKUP_VA,
                take: 0,
                pin: &[0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x20],
            },
            (|asm| {
                asm.mov(qword_ptr(rsp + 8), rbx)?;
                asm.push(rdi)?;
                asm.sub(rsp, 0x20)?;
                Ok(())
            }) as prologue_build::Assemble,
        )],
        "generated_place_name_prologues.rs",
    );

    generate(
        &[
            (
                PrologueSpec {
                    name: "SHOW_PROLOGUE",
                    doc: "`show(void* OSM, int groupId)`: eight callee-saved pushes and a 0x188\n\
                          frame. The one ERSC entry point WITHOUT an `endbr64`, which is what\n\
                          makes it a cheap \"is this the ersc.dll we measured\" discriminator.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc,
                    va: ERSC_SHOW_VA,
                    take: 12,
                    pin: &[
                        0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
                    ],
                },
                (|asm| {
                    ersc_callee_saved_pushes(asm)?;
                    asm.sub(rsp, 0x188)?;
                    Ok(())
                }) as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "INVADE_PROLOGUE",
                    doc: "`endbr64; push rsi; push rdi; sub rsp,0x28; mov rdi,[rcx+0x58]`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc,
                    va: ERSC_INVADE_ACTION_VA,
                    take: 0,
                    pin: ERSC_OPTION_ACTION_PIN,
                },
                ersc_option_action_prologue as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "CANCEL_PROLOGUE",
                    doc: "Byte-identical to [`INVADE_PROLOGUE`]; the two actions diverge only\n\
                          after the session load.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc,
                    va: ERSC_CANCEL_ACTION_VA,
                    take: 0,
                    pin: ERSC_OPTION_ACTION_PIN,
                },
                ersc_option_action_prologue as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "BUILD_LOBBY_KEY_PROLOGUE",
                    doc: "`BuildLobbyKey(ctx, std::string* out)`: the same eight pushes as\n\
                          `show`, then a 0x148 frame.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc,
                    va: ERSC_BUILD_LOBBY_KEY_VA,
                    take: 0,
                    pin: &[
                        0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
                        0x48, 0x81, 0xec, 0x48, 0x01, 0x00, 0x00,
                    ],
                },
                (|asm| {
                    ersc_callee_saved_pushes(asm)?;
                    asm.sub(rsp, 0x148)?;
                    Ok(())
                }) as prologue_build::Assemble,
            ),
        ],
        "generated_ersc_prologues.rs",
    );
}
