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
/// Every VA in this file is a 2.7.2.0 VA in `eldenring-deobf-1.17.2.bin`.
const HAS_SPECIAL_EFFECT_ID_VA: u64 = 0x1404fa7f0;
/// The `container->head == null` early-out `HasSpecialEffectId` jumps to (`+0x1e`, its own
/// `XOR AL,AL; RET`); the branch is part of the checked prologue, so its destination has to be
/// named too.
const HAS_SPECIAL_EFFECT_ID_EMPTY_RETURN_VA: u64 = 0x1404fa80e;
/// `int CS::SpecialEffect::Apply(SpecialEffect *container, int spEffectId, ChrIns *, ChrIns *,
/// FloatVector4 *, byte, bool, byte)`.
const APPLY_VA: u64 = 0x1404fb790;
/// `FUN_140d3d5f0(out, platformKind)` (1.16.2 dump name) -- the `LoadBalancerParam` row lookup
/// that `DLPanic`s when `SoloParamRepository` is null. See `guards::null_param_repository`.
const LOAD_BALANCER_PARAM_VA: u64 = 0x140d404a0;
/// `void CS::MapItemManImpl::GiveItems(...)`. See `mid_hooks::give_items`.
const GIVE_ITEMS_VA: u64 = 0x1405618b0;
/// Offsets into `GiveItems`, identical in 1.16.2, 1.17.1 and 2.7.2.0 (the 2.7.2.0 body has the
/// same instruction at every offset).
const GIVE_ITEMS_STORAGE_OVERFLOW_LOAD: u64 = 0x1d9;
const GIVE_ITEMS_STORAGE_ABSENT_JOIN: u64 = 0x2f3;
const GIVE_ITEMS_PICKUP_LOG_LOAD: u64 = 0x34a;
const GIVE_ITEMS_PICKUP_NOT_MINE: u64 = 0x3c1;
/// Each window's address, named so `verify-aob-patterns.py` can read the spec's `va:`.
const STORAGE_OVERFLOW_LOAD_VA: u64 = GIVE_ITEMS_VA + GIVE_ITEMS_STORAGE_OVERFLOW_LOAD;
const STORAGE_ABSENT_JOIN_VA: u64 = GIVE_ITEMS_VA + GIVE_ITEMS_STORAGE_ABSENT_JOIN;
const PICKUP_LOG_LOAD_VA: u64 = GIVE_ITEMS_VA + GIVE_ITEMS_PICKUP_LOG_LOAD;
const PICKUP_NOT_MINE_VA: u64 = GIVE_ITEMS_VA + GIVE_ITEMS_PICKUP_NOT_MINE;
/// `WorldChrManImp+0x1e508`, the local `PlayerIns`.
const WORLD_CHR_MAN_MAIN_PLAYER: i64 = 0x1e508;
/// `ChrIns` vtable slot `+0x168`, `GetPlayerGameData`.
const CHR_INS_GET_PLAYER_GAME_DATA_SLOT: i32 = 0x168;
/// The SEH frame marker the prologue stores; named so the assembled `MOV` is not a bare literal.
const SEH_FRAME_UNINITIALISED: i32 = -2;

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    generate(
        &[
            (
                PrologueSpec {
                    name: "HAS_SPECIAL_EFFECT_ID_PROLOGUE",
                    doc: "2.7.2.0 prologue of `CS::SpecialEffect::HasSpecialEffectId`:\n\
                          `MOV RCX,[RCX+8]; TEST RCX,RCX; JZ +0x15`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
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
                    doc: "2.7.2.0 prologue of `CS::SpecialEffect::Apply`:\n\
                          `MOV [RSP+0x10],RBP; MOV [RSP+0x18],RSI`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
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
                doc: "2.7.2.0 prologue of the `LoadBalancerParam` row lookup:\n\
                      `PUSH R14; SUB RSP,0x40; MOV [RSP+0x20],-2`.",
                visibility: "pub(crate)",
                shape: Shape::Slice,
                image: Image::EldenRing2720,
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
    fn main_player_load(
        asm: &mut iced_x86::code_asm::CodeAssembler,
    ) -> Result<(), iced_x86::IcedError> {
        use iced_x86::code_asm::*;
        mov_r64_mem(asm, Register::RCX, Register::RCX, WORLD_CHR_MAN_MAIN_PLAYER)?;
        mov_r64_mem_base(asm, Register::RAX, Register::RCX)?;
        asm.call(qword_ptr(rax + CHR_INS_GET_PLAYER_GAME_DATA_SLOT))?;
        Ok(())
    }
    generate(
        &[
            (
                PrologueSpec {
                    name: "STORAGE_OVERFLOW_LOAD_WINDOW",
                    doc: "2.7.2.0 `GiveItems+0x1d9`, the storage-overflow branch's player load:\n\
                          `MOV RCX,[RCX+0x1e508]; MOV RAX,[RCX]; CALL [RAX+0x168]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: STORAGE_OVERFLOW_LOAD_VA,
                    take: 0,
                    pin: MAIN_PLAYER_LOAD_PIN,
                },
                main_player_load as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "PICKUP_LOG_LOAD_WINDOW",
                    doc: "2.7.2.0 `GiveItems+0x34a`, the pickup-log branch's player load:\n\
                          `MOV RCX,[RCX+0x1e508]; MOV RAX,[RCX]; CALL [RAX+0x168]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: PICKUP_LOG_LOAD_VA,
                    take: 0,
                    pin: MAIN_PLAYER_LOAD_PIN,
                },
                main_player_load as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "STORAGE_ABSENT_JOIN_WINDOW",
                    doc: "2.7.2.0 `GiveItems+0x2f3`, where the no-storage-chest branch rejoins:\n\
                          `MOV ESI,[RSP+0x34]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: STORAGE_ABSENT_JOIN_VA,
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
                    doc: "2.7.2.0 `GiveItems+0x3c1`, the branch for an item another player gave:\n\
                          `AND EDI,-2`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: PICKUP_NOT_MINE_VA,
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
    // The `CSFreeListMemorySystem` shutdown-assert window moved to `crates/er-crash-logging/build.rs`
    // with its patch on 2026-10-26: it is a vanilla crash, not a Seamless one.
}
