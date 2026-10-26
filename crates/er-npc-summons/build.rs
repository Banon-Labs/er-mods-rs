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
use prologue_build::{
    Assemble, Image, PrologueSpec, Shape, generate, mov_r64_mem, mov_r64_rm64, xor_r32_rm32,
};

const SUPPORT: &str = "../../build-support/prologue_build.rs";
/// The one place the two param getters' addresses and window offsets are written down.
const SITES: &str = "getter_sites.rs";

include!(concat!(env!("CARGO_MANIFEST_DIR"), "/getter_sites.rs"));

// Each getter window's virtual address, written as `GAME_IMAGE_BASE + (rva + offset) as u64`
// rather than through a helper, because `scripts/rva_symbols.py` evaluates arithmetic and not calls.
const BUDDY_PARAM_FRAME_VA: u64 =
    GAME_IMAGE_BASE + (GET_BUDDY_PARAM_RVA + GETTER_FRAME_WINDOW_OFFSET) as u64;
const BUDDY_PARAM_TABLE_VA: u64 =
    GAME_IMAGE_BASE + (GET_BUDDY_PARAM_RVA + GETTER_TABLE_WINDOW_OFFSET) as u64;
const BUDDY_PARAM_OUT_STORE_VA: u64 =
    GAME_IMAGE_BASE + (GET_BUDDY_PARAM_RVA + GETTER_OUT_STORE_WINDOW_OFFSET) as u64;
const BUDDY_PARAM_NEGATIVE_ID_STORE_VA: u64 =
    GAME_IMAGE_BASE + (GET_BUDDY_PARAM_RVA + GETTER_NEGATIVE_ID_STORE_WINDOW_OFFSET) as u64;
const CHARA_INIT_PARAM_FRAME_VA: u64 =
    GAME_IMAGE_BASE + (GET_CHARA_INIT_PARAM_RVA + GETTER_FRAME_WINDOW_OFFSET) as u64;
const CHARA_INIT_PARAM_TABLE_VA: u64 =
    GAME_IMAGE_BASE + (GET_CHARA_INIT_PARAM_RVA + GETTER_TABLE_WINDOW_OFFSET) as u64;
const CHARA_INIT_PARAM_OUT_STORE_VA: u64 =
    GAME_IMAGE_BASE + (GET_CHARA_INIT_PARAM_RVA + GETTER_OUT_STORE_WINDOW_OFFSET) as u64;
const CHARA_INIT_PARAM_NEGATIVE_ID_STORE_VA: u64 =
    GAME_IMAGE_BASE + (GET_CHARA_INIT_PARAM_RVA + GETTER_NEGATIVE_ID_STORE_WINDOW_OFFSET) as u64;

/// `mov qword [rsp+0x20],-2`, the frame marker both getters store third.
const FRAME_MARKER_SLOT: i32 = 0x20;
const FRAME_MARKER: i32 = -2;
/// `GetParamResCap`'s table index for each getter: BuddyParam is solo param 128, CharaInitParam 23.
const BUDDY_PARAM_TABLE: i32 = 0x80;
const CHARA_INIT_PARAM_TABLE: i32 = 0x17;
/// The out struct's row pointer, as a displacement.
const OUT_ROW: i32 = GETTER_OUT_ROW_SLOT as i32;

/// "A menu has the mouse", `bool(CSMenuManImp*)`, 2.7.2.0 `0x140766b00`
/// (`addr::MENU_HAS_MOUSE_RVA`).
const MENU_HAS_MOUSE_VA: u64 = 0x140766b00;

/// `CS::PlayerIns::CopyAnother(PlayerIns* to, PlayerIns* from)`, 2.7.2.0 `0x140654ac0`
/// (`addr::COPY_ANOTHER_RVA`; 1.16.2 `0x1406537c0`).
const COPY_ANOTHER_VA: u64 = 0x140654ac0;
/// Its early return when `from` is null.
const COPY_ANOTHER_NULL_RETURN_VA: u64 = 0x140654c5e;

/// 2.7.2.0 `FUN_1407786b0`, the producer of the seven `ChrFriendlyTagEntry` rows at
/// `CSFeManImp+0x5c60` (`addr::tags::FRIENDLY_TAG_PRODUCER`).
const FRIENDLY_TAG_PRODUCER_VA: u64 = 0x1407786b0;
/// `test r15d,r15d`: the start of the per-slot "is slot i a party member" test.
const TAG_SLOT_TEST: u64 = 0x2f8;
/// The slot-empty branch both halves of that test jump to.
const TAG_SLOT_EMPTY: u64 = 0xa7a;
/// `mov rcx,[WorldChrMan]`, right after `GetFieldInsHandleByMemberIndex` returns.
const TAG_SLOT_HANDLE_READY: u64 = 0x31c;
/// `mov [rsp+0x33],al`, storing what `GetRoleNameColor` returned.
const TAG_COLOUR_STORE: u64 = 0x607;
/// 2.7.2.0 `CSChrDamageModule::UpdateUI` (`addr::tags::DAMAGE_UPDATE_UI`).
const DAMAGE_UPDATE_UI_VA: u64 = 0x1404498c0;
/// `cmp [rsp+0x31],r12b`: the first test of the enemy-bar selection.
const ENEMY_BAR_TEST: u64 = 0x26e;
/// `test bpl,bpl`: the tail the enemy-bar call and every no-bar branch share.
const ENEMY_BAR_TAIL: u64 = 0x375;
/// The 2.7.2.0 `WorldChrManImp*` global the handle-ready instruction reads
/// (`er_game_base::rva::WORLD_CHR_MAN_GLOBAL_RVA`).
const WORLD_CHR_MAN_GLOBAL_VA: u64 = 0x143d6d018;
/// Each window's address, named so `verify-aob-patterns.py` can read the spec's `va:`.
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

/// `test rdx,rdx; je <return>; mov rax,rsp; push rdi; sub rsp,0x70; mov qword [rax-0x58],-2;
/// mov [rax+8],rbx; mov [rax+0x20],rsi`: `CopyAnother`'s null check and frame.
fn copy_another(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.test(rdx, rdx)?;
    asm.je(COPY_ANOTHER_NULL_RETURN_VA)?;
    // The game encodes it `8b c4` (`mov r64, r/m64`); the assembler would pick `89 e0`.
    asm.add_instruction(Instruction::with2(
        Code::Mov_r64_rm64,
        Register::RAX,
        Register::RSP,
    )?)?;
    asm.push(rdi)?;
    asm.sub(rsp, 0x70)?;
    asm.mov(qword_ptr(rax - 0x58), -2)?;
    asm.mov(qword_ptr(rax + 8), rbx)?;
    asm.mov(qword_ptr(rax + 0x20), rsi)?;
    Ok(())
}

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

/// `mov qword [rsp+0x20],-2`, the third instruction of both getters.
fn getter_frame(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.mov(qword_ptr(rsp + FRAME_MARKER_SLOT), FRAME_MARKER)?;
    Ok(())
}

/// `xor r8d,r8d; mov edx,0x80`: BuddyParam's table index handed to `GetParamResCap`.
fn buddy_param_table(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    xor_r32_rm32(asm, Register::R8D, Register::R8D)?;
    asm.mov(edx, BUDDY_PARAM_TABLE)?;
    Ok(())
}

/// `xor r8d,r8d; lea edx,[r8+0x17]`: CharaInitParam's table index handed to `GetParamResCap`.
fn chara_init_param_table(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    xor_r32_rm32(asm, Register::R8D, Register::R8D)?;
    asm.lea(edx, ptr(r8 + CHARA_INIT_PARAM_TABLE))?;
    Ok(())
}

/// `mov [rsi],edi; mov [rsi+8],rbx`: the id and the row pointer into the out struct.
fn getter_out_store(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.mov(dword_ptr(rsi), edi)?;
    asm.mov(qword_ptr(rsi + OUT_ROW), rbx)?;
    Ok(())
}

/// `mov [rcx],edi; mov [rcx+8],rbx`: the same stores on the negative-id path.
fn getter_negative_id_store(asm: &mut CodeAssembler) -> Result<(), IcedError> {
    asm.mov(dword_ptr(rcx), edi)?;
    asm.mov(qword_ptr(rcx + OUT_ROW), rbx)?;
    Ok(())
}

fn main() {
    prologue_build::declare_rerun(SUPPORT);
    prologue_build::declare_rerun(SITES);
    generate(
        &[
            (
                PrologueSpec {
                    name: "MENU_HAS_MOUSE_PROLOGUE",
                    doc: "`mov [rsp+0x10],rbx; mov [rsp+0x18],rbp; mov [rsp+0x20],rsi; push rdi;\n\
                      sub rsp,0x20; cmp byte [rcx+0x1a],0` -- the opening of the 2.7.2.0\n\
                      menu-has-the-mouse predicate.",
                    visibility: "pub(crate)",
                    shape: Shape::Array,
                    image: Image::EldenRing2720,
                    va: MENU_HAS_MOUSE_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x89, 0x5c, 0x24, 0x10, 0x48, 0x89, 0x6c, 0x24, 0x18, 0x48, 0x89,
                        0x74, 0x24, 0x20, 0x57, 0x48, 0x83, 0xec, 0x20, 0x80, 0x79, 0x1a, 0x00,
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
            ),
            (
                PrologueSpec {
                    name: "COPY_ANOTHER_PROLOGUE",
                    doc: "`test rdx,rdx; je <return>; mov rax,rsp; push rdi; sub rsp,0x70;\n\
                      mov qword [rax-0x58],-2; mov [rax+8],rbx; mov [rax+0x20],rsi` -- the\n\
                      opening of the 2.7.2.0 `CS::PlayerIns::CopyAnother`.",
                    visibility: "pub(crate)",
                    shape: Shape::Array,
                    image: Image::EldenRing2720,
                    va: COPY_ANOTHER_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x85, 0xd2, 0x0f, 0x84, 0x95, 0x01, 0x00, 0x00, 0x48, 0x8b, 0xc4,
                        0x57, 0x48, 0x83, 0xec, 0x70, 0x48, 0xc7, 0x40, 0xa8, 0xfe, 0xff, 0xff,
                        0xff, 0x48, 0x89, 0x58, 0x08, 0x48, 0x89, 0x70, 0x20,
                    ],
                },
                copy_another as Assemble,
            ),
        ],
        "generated_prologues.rs",
    );

    // The companions' overhead tags. Each hook's window is every instruction ilhook's 14-byte jump
    // overwrites, in full, so the bytes it moves are exactly the bytes checked; a resume target is
    // pinned too. All five are read in the 2.7.2.0 image (`mid_hooks/tag_list.rs`).
    generate(
        &[
            (
                PrologueSpec {
                    name: "TAG_SLOT_TEST_WINDOW",
                    doc: "2.7.2.0 `FUN_1407786b0+0x2f8`, the friendly tag producer's per-slot test:\n\
                          `TEST R15D,R15D; JS +0xa7a; CMP R15D,[RSP+0x50]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
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
                    doc: "2.7.2.0 `FUN_1407786b0+0x31c`, the instruction after\n\
                          `GetFieldInsHandleByMemberIndex` returns: `MOV RCX,[WorldChrMan]`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: TAG_SLOT_HANDLE_READY_VA,
                    take: 0,
                    pin: &[0x48, 0x8b, 0x0d, 0x45, 0x46, 0x5f, 0x03],
                },
                tag_slot_handle_ready as Assemble,
            ),
            (
                PrologueSpec {
                    name: "TAG_COLOUR_STORE_WINDOW",
                    doc: "2.7.2.0 `FUN_1407786b0+0x607`, the store of `GetRoleNameColor`'s answer:\n\
                          `MOV [RSP+0x33],AL; LEA RDX,[RBP+0x30]; MOV R12,[RBP-0x70]; MOV RCX,R12`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
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
                    doc: "2.7.2.0 `CSChrDamageModule::UpdateUI+0x26e`, the enemy-bar selection:\n\
                          `CMP [RSP+0x31],R12B; JNE +0x375; CMP [RSP+0x32],R12B`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
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
                    doc: "2.7.2.0 `CSChrDamageModule::UpdateUI+0x375`, the shared tail after the\n\
                          enemy-bar call: `TEST BPL,BPL`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: ENEMY_BAR_TAIL_VA,
                    take: 0,
                    pin: &[0x40, 0x84, 0xed],
                },
                enemy_bar_tail as Assemble,
            ),
        ],
        "generated_tag_windows.rs",
    );

    // The two typed getters the Mimic Tear's row copies are served through (`getter_sites.rs`).
    // Each window is checked at the running getter before its out struct is trusted: the frame
    // marker, the table index that names the table, and both stores of `out = {id, row*}`.
    // Read in eldenring-deobf-1.17.2.bin.
    generate(
        &[
            (
                PrologueSpec {
                    name: "BUDDY_PARAM_FRAME_WINDOW",
                    doc: "2.7.2.0 `GetBuddyParam+0x6`: `MOV qword [RSP+0x20],-2`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: BUDDY_PARAM_FRAME_VA,
                    take: 0,
                    pin: &[0x48, 0xc7, 0x44, 0x24, 0x20, 0xfe, 0xff, 0xff, 0xff],
                },
                getter_frame as Assemble,
            ),
            (
                PrologueSpec {
                    name: "BUDDY_PARAM_TABLE_WINDOW",
                    doc: "2.7.2.0 `GetBuddyParam+0x67`: `XOR R8D,R8D; MOV EDX,0x80`, the BuddyParam\n\
                          table index handed to `GetParamResCap`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: BUDDY_PARAM_TABLE_VA,
                    take: 0,
                    pin: &[0x45, 0x33, 0xc0, 0xba, 0x80, 0x00, 0x00, 0x00],
                },
                buddy_param_table as Assemble,
            ),
            (
                PrologueSpec {
                    name: "BUDDY_PARAM_OUT_STORE_WINDOW",
                    doc: "2.7.2.0 `GetBuddyParam+0x145`: `MOV [RSI],EDI; MOV [RSI+8],RBX`, the id\n\
                          and the row pointer into the out struct.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: BUDDY_PARAM_OUT_STORE_VA,
                    take: 0,
                    pin: &[0x89, 0x3e, 0x48, 0x89, 0x5e, 0x08],
                },
                getter_out_store as Assemble,
            ),
            (
                PrologueSpec {
                    name: "BUDDY_PARAM_NEGATIVE_ID_STORE_WINDOW",
                    doc: "2.7.2.0 `GetBuddyParam+0x14d`: `MOV [RCX],EDI; MOV [RCX+8],RBX`, the same\n\
                          stores for a negative id.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: BUDDY_PARAM_NEGATIVE_ID_STORE_VA,
                    take: 0,
                    pin: &[0x89, 0x39, 0x48, 0x89, 0x59, 0x08],
                },
                getter_negative_id_store as Assemble,
            ),
            (
                PrologueSpec {
                    name: "CHARA_INIT_PARAM_FRAME_WINDOW",
                    doc: "2.7.2.0 `GetCharaInitParam+0x6`: `MOV qword [RSP+0x20],-2`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: CHARA_INIT_PARAM_FRAME_VA,
                    take: 0,
                    pin: &[0x48, 0xc7, 0x44, 0x24, 0x20, 0xfe, 0xff, 0xff, 0xff],
                },
                getter_frame as Assemble,
            ),
            (
                PrologueSpec {
                    name: "CHARA_INIT_PARAM_TABLE_WINDOW",
                    doc: "2.7.2.0 `GetCharaInitParam+0x67`: `XOR R8D,R8D; LEA EDX,[R8+0x17]`, the\n\
                          CharaInitParam table index handed to `GetParamResCap`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: CHARA_INIT_PARAM_TABLE_VA,
                    take: 0,
                    pin: &[0x45, 0x33, 0xc0, 0x41, 0x8d, 0x50, 0x17],
                },
                chara_init_param_table as Assemble,
            ),
            (
                PrologueSpec {
                    name: "CHARA_INIT_PARAM_OUT_STORE_WINDOW",
                    doc: "2.7.2.0 `GetCharaInitParam+0x145`: `MOV [RSI],EDI; MOV [RSI+8],RBX`, the\n\
                          id and the row pointer into the out struct.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: CHARA_INIT_PARAM_OUT_STORE_VA,
                    take: 0,
                    pin: &[0x89, 0x3e, 0x48, 0x89, 0x5e, 0x08],
                },
                getter_out_store as Assemble,
            ),
            (
                PrologueSpec {
                    name: "CHARA_INIT_PARAM_NEGATIVE_ID_STORE_WINDOW",
                    doc: "2.7.2.0 `GetCharaInitParam+0x14d`: `MOV [RCX],EDI; MOV [RCX+8],RBX`, the\n\
                          same stores for a negative id.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing2720,
                    va: CHARA_INIT_PARAM_NEGATIVE_ID_STORE_VA,
                    take: 0,
                    pin: &[0x89, 0x39, 0x48, 0x89, 0x59, 0x08],
                },
                getter_negative_id_store as Assemble,
            ),
        ],
        "generated_getter_windows.rs",
    );
}
