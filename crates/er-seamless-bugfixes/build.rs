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
use prologue_build::{Image, PrologueSpec, Shape, generate, mov_r64_mem, mov_r64_mem_base};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// `bool CS::SpecialEffect::HasSpecialEffectId(SpecialEffect *container, uint spEffectId)`.
const HAS_SPECIAL_EFFECT_ID_VA: u64 = 0x1404f9940;
/// The `container->head == null` early-out `HasSpecialEffectId` jumps to; the branch is part of
/// the checked prologue, so its destination has to be named too.
const HAS_SPECIAL_EFFECT_ID_EMPTY_RETURN_VA: u64 = 0x1404f995e;
/// `int CS::SpecialEffect::Apply(SpecialEffect *container, int spEffectId, ChrIns *, ChrIns *,
/// FloatVector4 *, byte, bool, byte)`.
const APPLY_VA: u64 = 0x1404fa8e0;
/// `FUN_140d3d5f0(out, platformKind)` -- the `LoadBalancerParam` row lookup that `DLPanic`s when
/// `SoloParamRepository` is null. See `guards::null_param_repository`.
const LOAD_BALANCER_PARAM_VA: u64 = 0x140d3d5f0;
/// `void CS::MapItemManImpl::GiveItems(...)`. See `mid_hooks::give_items`.
const GIVE_ITEMS_VA: u64 = 0x1405605b0;
/// Offsets into `GiveItems`, identical in 1.16.2 and 1.17.1 (the body is `IDENTICAL-WHOLE`).
const GIVE_ITEMS_STORAGE_OVERFLOW_LOAD: u64 = 0x1d9;
const GIVE_ITEMS_STORAGE_ABSENT_JOIN: u64 = 0x2f3;
const GIVE_ITEMS_PICKUP_LOG_LOAD: u64 = 0x34a;
const GIVE_ITEMS_PICKUP_NOT_MINE: u64 = 0x3c1;
/// `WorldChrManImp+0x1e508`, the local `PlayerIns`.
const WORLD_CHR_MAN_MAIN_PLAYER: i64 = 0x1e508;
/// `ChrIns` vtable slot `+0x168`, `GetPlayerGameData`.
const CHR_INS_GET_PLAYER_GAME_DATA_SLOT: i32 = 0x168;
/// The SEH frame marker the prologue stores; named so the assembled `MOV` is not a bare literal.
const SEH_FRAME_UNINITIALISED: i32 = -2;
/// The leak assertion inside `CS::CSFreeListMemorySystem`'s shutdown, at the `CMP` that decides
/// whether to break. See `patches::freelist_shutdown_assert`.
const FREELIST_SHUTDOWN_ASSERT_VA: u64 = 0x140c57670;
/// Where the assertion's `JZ` lands: one byte past the `INT3`, which is also where the `INT3`
/// itself falls through to. Naming it is the whole argument for the patch, so it is named here
/// rather than encoded as a displacement.
const FREELIST_SHUTDOWN_ASSERT_RESUME_VA: u64 = 0x140c57677;
/// Offset within the node of the "this thread-local free-list is still checked out" flag the
/// assertion tests. Set by the registrar at `0x140c579f0`.
const FREE_LIST_NODE_IN_USE_FLAG: i64 = 0x18;
/// Offset of the allocator the shutdown loop calls `Free` through, loaded by the instruction
/// immediately after the `INT3` -- the one that pins the far side of the window.
const FREE_LIST_SYSTEM_ALLOCATOR: i64 = 8;

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

    generate(
        &[(
            PrologueSpec {
                name: "LOAD_BALANCER_PARAM_PROLOGUE",
                doc: "1.16.2 prologue of the `LoadBalancerParam` row lookup:\n\
                      `PUSH R14; SUB RSP,0x40; MOV [RSP+0x20],-2`.",
                visibility: "pub(crate)",
                shape: Shape::Slice,
                image: Image::EldenRing,
                va: LOAD_BALANCER_PARAM_VA,
                take: 0,
                pin: &[
                    0x41, 0x56, 0x48, 0x83, 0xec, 0x40, 0x48, 0xc7, 0x44, 0x24, 0x20, 0xfe, 0xff,
                    0xff, 0xff,
                ],
            },
            (|asm| {
                use iced_x86::code_asm::*;
                asm.push(r14)?;
                asm.sub(rsp, 0x40)?;
                asm.mov(qword_ptr(rsp + 0x20), SEH_FRAME_UNINITIALISED)?;
                Ok(())
            }) as prologue_build::Assemble,
        )],
        "generated_null_param_repository_prologues.rs",
    );

    // `GiveItems` loads the local player twice without testing it. Each load is the first of three
    // instructions the mid hook overwrites (ilhook writes a 14-byte jump), and the window is those
    // three in full, so the bytes it moves are exactly the bytes checked here.
    const MAIN_PLAYER_LOAD_PIN: &[u8] = &[
        0x48, 0x8b, 0x89, 0x08, 0xe5, 0x01, 0x00, 0x48, 0x8b, 0x01, 0xff, 0x90, 0x68, 0x01, 0x00,
        0x00,
    ];
    let main_player_load: prologue_build::Assemble = |asm| {
        use iced_x86::code_asm::*;
        mov_r64_mem(asm, Register::RCX, Register::RCX, WORLD_CHR_MAN_MAIN_PLAYER)?;
        mov_r64_mem_base(asm, Register::RAX, Register::RCX)?;
        asm.call(qword_ptr(rax + CHR_INS_GET_PLAYER_GAME_DATA_SLOT))?;
        Ok(())
    };
    generate(
        &[
            (
                PrologueSpec {
                    name: "STORAGE_OVERFLOW_LOAD_WINDOW",
                    doc: "1.16.2 `GiveItems+0x1d9`, the storage-overflow branch's player load:\n\
                          `MOV RCX,[RCX+0x1e508]; MOV RAX,[RCX]; CALL [RAX+0x168]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: GIVE_ITEMS_VA + GIVE_ITEMS_STORAGE_OVERFLOW_LOAD,
                    take: 0,
                    pin: MAIN_PLAYER_LOAD_PIN,
                },
                main_player_load,
            ),
            (
                PrologueSpec {
                    name: "PICKUP_LOG_LOAD_WINDOW",
                    doc: "1.16.2 `GiveItems+0x34a`, the pickup-log branch's player load:\n\
                          `MOV RCX,[RCX+0x1e508]; MOV RAX,[RCX]; CALL [RAX+0x168]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: GIVE_ITEMS_VA + GIVE_ITEMS_PICKUP_LOG_LOAD,
                    take: 0,
                    pin: MAIN_PLAYER_LOAD_PIN,
                },
                main_player_load,
            ),
            (
                PrologueSpec {
                    name: "STORAGE_ABSENT_JOIN_WINDOW",
                    doc: "1.16.2 `GiveItems+0x2f3`, where the no-storage-chest branch rejoins:\n\
                          `MOV ESI,[RSP+0x34]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: GIVE_ITEMS_VA + GIVE_ITEMS_STORAGE_ABSENT_JOIN,
                    take: 0,
                    pin: &[0x8b, 0x74, 0x24, 0x34],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.mov(esi, dword_ptr(rsp + 0x34))?;
                    Ok(())
                }) as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "PICKUP_NOT_MINE_WINDOW",
                    doc: "1.16.2 `GiveItems+0x3c1`, the branch for an item another player gave:\n\
                          `AND EDI,-2`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: GIVE_ITEMS_VA + GIVE_ITEMS_PICKUP_NOT_MINE,
                    take: 0,
                    pin: &[0x83, 0xe7, 0xfe],
                },
                (|asm| {
                    use iced_x86::code_asm::*;
                    asm.and(edi, -2)?;
                    Ok(())
                }) as prologue_build::Assemble,
            ),
        ],
        "generated_give_items_windows.rs",
    );

    generate(
        &[(
            PrologueSpec {
                name: "FREELIST_SHUTDOWN_ASSERT_WINDOW",
                doc: "1.16.2 bytes of the leak assertion in `CS::CSFreeListMemorySystem`'s\n\
                      shutdown: `CMP byte [RDX+0x18],0; JZ +1; INT3; MOV RCX,[RDI+8]`.\n\
                      The `JZ` and the `MOV` are in the window on purpose -- they are what prove\n\
                      the `INT3` is advisory, and they pin an address a bare `0xcc` could not.",
                visibility: "pub(crate)",
                shape: Shape::Slice,
                image: Image::EldenRing,
                va: FREELIST_SHUTDOWN_ASSERT_VA,
                take: 0,
                pin: &[
                    0x80, 0x7a, 0x18, 0x00, 0x74, 0x01, 0xcc, 0x48, 0x8b, 0x4f, 0x08,
                ],
            },
            (|asm| {
                use iced_x86::code_asm::*;
                asm.cmp(byte_ptr(rdx + FREE_LIST_NODE_IN_USE_FLAG), 0)?;
                asm.jz(FREELIST_SHUTDOWN_ASSERT_RESUME_VA)?;
                asm.int3()?;
                mov_r64_mem(
                    asm,
                    Register::RCX,
                    Register::RDI,
                    FREE_LIST_SYSTEM_ALLOCATOR,
                )?;
                Ok(())
            }) as prologue_build::Assemble,
        )],
        "generated_freelist_shutdown_assert.rs",
    );
}
