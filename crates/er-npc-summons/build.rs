//! Generates the prologue and mid-hook windows this DLL byte-checks, from named `iced-x86`
//! instructions.
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
use iced_x86::{Code, Instruction, MemoryOperand, Register};
use prologue_build::{Assemble, Image, PrologueSpec, Shape, generate, mov_r64_mem, mov_r64_rm64};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// "A menu has the mouse", `bool(CSMenuManImp*)`, 1.17 `0x140766650`. Below the `0xafefe9`
/// boundary, so 1.17.0 and 1.17.1 have it at the same address with the same bytes.
const MENU_HAS_MOUSE_1170_VA: u64 = 0x140766650;

/// 1.16.2 `FUN_140777380`, the producer of the seven `ChrFriendlyTagEntry` rows at
/// `CSFeManImp+0x5c60` (1.17.1 `0x140778200`). The body is `IDENTICAL-WHOLE` between the builds,
/// so every offset below holds in both.
const FRIENDLY_TAG_PRODUCER_VA: u64 = 0x140777380;
/// `test r15d,r15d`: the start of the per-slot "is slot i a party member" test.
const TAG_SLOT_TEST: u64 = 0x2f8;
/// The slot-empty branch both halves of that test jump to.
const TAG_SLOT_EMPTY: u64 = 0xa7a;
/// `mov rcx,[WorldChrMan]`, right after `GetFieldInsHandleByMemberIndex` returns.
const TAG_SLOT_HANDLE_READY: u64 = 0x31c;
/// `mov [rsp+0x33],al`, storing what `GetRoleNameColor` returned.
const TAG_COLOUR_STORE: u64 = 0x607;
/// 1.16.2 `CSChrDamageModule::UpdateUI` (1.17.1 `0x1404497e0`), `IDENTICAL-WHOLE` as well.
const DAMAGE_UPDATE_UI_VA: u64 = 0x140449280;
/// `cmp [rsp+0x31],r12b`: the first test of the enemy-bar selection.
const ENEMY_BAR_TEST: u64 = 0x26e;
/// `test bpl,bpl`: the tail the enemy-bar call and every no-bar branch share.
const ENEMY_BAR_TAIL: u64 = 0x375;
/// The 1.16.2 `WorldChrManImp*` global the handle-ready instruction reads
/// (`er_game_base::rva::WORLD_CHR_MAN_GLOBAL_RVA`).
const WORLD_CHR_MAN_GLOBAL_VA: u64 = 0x143d65f88;
/// Each window's address, named so `verify-aob-patterns-1170.py` can read the spec's `va:`.
const TAG_SLOT_TEST_VA: u64 = FRIENDLY_TAG_PRODUCER_VA + TAG_SLOT_TEST;
const TAG_SLOT_EMPTY_VA: u64 = FRIENDLY_TAG_PRODUCER_VA + TAG_SLOT_EMPTY;
const TAG_SLOT_HANDLE_READY_VA: u64 = FRIENDLY_TAG_PRODUCER_VA + TAG_SLOT_HANDLE_READY;
const TAG_COLOUR_STORE_VA: u64 = FRIENDLY_TAG_PRODUCER_VA + TAG_COLOUR_STORE;
const ENEMY_BAR_TEST_VA: u64 = DAMAGE_UPDATE_UI_VA + ENEMY_BAR_TEST;
const ENEMY_BAR_TAIL_VA: u64 = DAMAGE_UPDATE_UI_VA + ENEMY_BAR_TAIL;
/// The producer's local holding the party's member count, `[rsp+0x50]`.
const TAG_MEMBER_COUNT_LOCAL: i32 = 0x50;
/// UpdateUI's `DrawTagOffscreen` and party-member locals, `[rsp+0x31]` and `[rsp+0x32]`.
const ENEMY_BAR_OFFSCREEN_LOCAL: i32 = 0x31;
const ENEMY_BAR_PARTY_LOCAL: i32 = 0x32;

/// `test r15d,r15d; js <empty>; cmp r15d,[rsp+0x50]`: the 14 bytes the tag-slot hook moves.
fn tag_slot_test(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.test(r15d, r15d)?;
    asm.js(TAG_SLOT_EMPTY_VA)?;
    asm.cmp(r15d, dword_ptr(rsp + TAG_MEMBER_COUNT_LOCAL))?;
    Ok(())
}

/// `mov rcx,[rip -> WorldChrMan]`, where the tag-slot hook resumes.
fn tag_slot_handle_ready(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(
        Code::Mov_r64_rm64,
        Register::RCX,
        MemoryOperand::with_base_displ(Register::RIP, WORLD_CHR_MAN_GLOBAL_VA as i64),
    )?)
}

/// `mov [rsp+0x33],al; lea rdx,[rbp+0x30]; mov r12,[rbp-0x70]; mov rcx,r12`: the 15 bytes the
/// tag-colour hook moves.
fn tag_colour_store(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.mov(byte_ptr(rsp + 0x33), al)?;
    asm.lea(rdx, qword_ptr(rbp + 0x30))?;
    mov_r64_mem(asm, Register::R12, Register::RBP, -0x70)?;
    mov_r64_rm64(asm, Register::RCX, Register::R12)?;
    Ok(())
}

/// `cmp [rsp+0x31],r12b; jne <tail>; cmp [rsp+0x32],r12b`: the 16 bytes the enemy-bar hook moves.
fn enemy_bar_test(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.cmp(byte_ptr(rsp + ENEMY_BAR_OFFSCREEN_LOCAL), r12b)?;
    asm.jne(ENEMY_BAR_TAIL_VA)?;
    asm.cmp(byte_ptr(rsp + ENEMY_BAR_PARTY_LOCAL), r12b)?;
    Ok(())
}

/// `test bpl,bpl`, where the enemy-bar hook resumes for a companion.
fn enemy_bar_tail(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.test(bpl, bpl)?;
    Ok(())
}

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

    // The companions' overhead tags. Each hook's window is every instruction ilhook's 14-byte jump
    // overwrites, in full, so the bytes it moves are exactly the bytes checked; a resume target is
    // pinned too. All five were read in both images on 2026-10-19 (`mid_hooks/tag_list.rs`).
    generate(
        &[
            (
                PrologueSpec {
                    name: "TAG_SLOT_TEST_WINDOW",
                    doc: "1.16.2 `FUN_140777380+0x2f8`, the friendly tag producer's per-slot test:\n\
                          `TEST R15D,R15D; JS +0xa7a; CMP R15D,[RSP+0x50]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: TAG_SLOT_TEST_VA,
                    take: 0,
                    pin: &[
                        0x45, 0x85, 0xff, 0x0f, 0x88, 0x79, 0x07, 0x00, 0x00, 0x44, 0x3b, 0x7c,
                        0x24, 0x50,
                    ],
                },
                tag_slot_test as Assemble,
            ),
            (
                PrologueSpec {
                    name: "TAG_SLOT_HANDLE_READY_WINDOW",
                    doc: "1.16.2 `FUN_140777380+0x31c`, the instruction after\n\
                          `GetFieldInsHandleByMemberIndex` returns: `MOV RCX,[WorldChrMan]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: TAG_SLOT_HANDLE_READY_VA,
                    take: 0,
                    pin: &[0x48, 0x8b, 0x0d, 0xe5, 0xe8, 0x5e, 0x03],
                },
                tag_slot_handle_ready as Assemble,
            ),
            (
                PrologueSpec {
                    name: "TAG_COLOUR_STORE_WINDOW",
                    doc: "1.16.2 `FUN_140777380+0x607`, the store of `GetRoleNameColor`'s answer:\n\
                          `MOV [RSP+0x33],AL; LEA RDX,[RBP+0x30]; MOV R12,[RBP-0x70]; MOV RCX,R12`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: TAG_COLOUR_STORE_VA,
                    take: 0,
                    pin: &[
                        0x88, 0x44, 0x24, 0x33, 0x48, 0x8d, 0x55, 0x30, 0x4c, 0x8b, 0x65, 0x90,
                        0x49, 0x8b, 0xcc,
                    ],
                },
                tag_colour_store as Assemble,
            ),
            (
                PrologueSpec {
                    name: "ENEMY_BAR_TEST_WINDOW",
                    doc: "1.16.2 `CSChrDamageModule::UpdateUI+0x26e`, the enemy-bar selection:\n\
                          `CMP [RSP+0x31],R12B; JNE +0x375; CMP [RSP+0x32],R12B`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: ENEMY_BAR_TEST_VA,
                    take: 0,
                    pin: &[
                        0x44, 0x38, 0x64, 0x24, 0x31, 0x0f, 0x85, 0xfc, 0x00, 0x00, 0x00, 0x44,
                        0x38, 0x64, 0x24, 0x32,
                    ],
                },
                enemy_bar_test as Assemble,
            ),
            (
                PrologueSpec {
                    name: "ENEMY_BAR_TAIL_WINDOW",
                    doc: "1.16.2 `CSChrDamageModule::UpdateUI+0x375`, the shared tail after the\n\
                          enemy-bar call: `TEST BPL,BPL`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: ENEMY_BAR_TAIL_VA,
                    take: 0,
                    pin: &[0x40, 0x84, 0xed],
                },
                enemy_bar_tail as Assemble,
            ),
        ],
        "generated_tag_windows.rs",
    );
}
