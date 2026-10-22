//! The prologues this crate byte-checks before it calls into the game, from named instructions.
//!
//! Two tables, one generator. The `CS::SoftwareKeyboard` recipe drives two surfaces -- the save
//! picker's path editor and the System>Quit link field -- while the message-box and save-request
//! recipes drive the Save Game row's flow. Both moved here from `er-quickload`'s build script with
//! the code that uses them, because a standalone shell has to build with no product crate in the
//! profile and the constants were `pub(crate)` to a crate this one cannot depend on.
//!
//! The save-flow table arrived on 2026-09-12 and replaced the keyboard table rather than joining
//! it, which left `software_keyboard.rs` including a file nothing wrote. That is not a degraded
//! check, it is `couldn't read .../generated_software_keyboard_prologues.rs` and a crate that does
//! not compile at all, so every shell built from this crate was unbuildable. Both `generate` calls
//! are below for that reason. Generating either from a second build script would write each
//! address out twice, which `scripts/check-rva-alias-drift.py` refuses.
//!
//! See `build-support/prologue_build.rs` for why these are generated from named instructions rather
//! than hand-typed, and for what verifies the result against the game image.

#[allow(dead_code)]
mod prologue_build {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../build-support/prologue_build.rs"
    ));
}

use iced_x86::Register;
use iced_x86::code_asm::*;
use prologue_build::{
    Assemble, Image, PrologueSpec, Shape, cmp_r32_rm32, generate, mov_r32_mem, mov_r64_mem_base,
    mov_r64_rm64, mov_rax_rip_absolute, rex_push,
};

const SUPPORT: &str = "../../build-support/prologue_build.rs";

/// The SoftwareKeyboard recipe the save-picker path editor drives.
const SOFTWARE_KEYBOARD_JOB_CTOR_VA: u64 = 0x14081be30;
const SOFTWARE_KEYBOARD_RESULT_GATE_VA: u64 = 0x14081d3d0;
const SOFTWARE_KEYBOARD_TERMINAL_CALLBACK_VA: u64 = 0x14081d220;
const SOFTWARE_KEYBOARD_VALIDATOR_INIT_VA: u64 = 0x140e70920;
const SOFTWARE_KEYBOARD_VALIDATOR_DTOR_VA: u64 = 0x140e70960;
const SOFTWARE_KEYBOARD_ENTER_NAME_VA: u64 = 0x140e70c00;
const SOFTWARE_KEYBOARD_SET_INITIAL_VA: u64 = 0x140e709f0;
const SOFTWARE_KEYBOARD_SET_MAX_VA: u64 = 0x142416ee0;
const GAME_HEAP_ALLOC_VA: u64 = 0x141eb9ed0;
/// The EnterName preset's window stops inside its `sub rsp,0x70`, so the assembled sequence is
/// one byte longer than the constant.
const SOFTWARE_KEYBOARD_ENTER_NAME_CHECKED_BYTES: usize = 7;

/// The `mov [rsp+8],rcx; push rbx; sub rsp,0x30` opening shared by the validator's init and
/// dtor -- byte-identical, which is why both are checked at their own RVA.
fn validator_prologue(asm: &mut CodeAssembler) -> Result<(), iced_x86::IcedError> {
    asm.mov(qword_ptr(rsp + 8), rcx)?;
    asm.push(rbx)?;
    asm.sub(rsp, 0x30)
}

const VALIDATOR_PIN: &[u8] = &[0x48, 0x89, 0x4c, 0x24, 0x08, 0x53, 0x48, 0x83, 0xec, 0x30];

/// `GLOBAL_CSGameMan`, at its 1.17 address. The two retractions load it RIP-relative; naming the
/// absolute address lets iced compute each site's displacement instead of transcribing it.
///
/// 1.16.2 had it at `0x143d69918`; 1.17 moved it `+0x4070`, along with most of `.data`. That is
/// carried in `docs/recon/rva-map-1162-to-1170.data.tsv` as `GAME_MAN_SINGLETON_RVA` on 136 of
/// 137 independent references, and confirmed here by reading the 1.17 image: all three sites that
/// load it (`0x140679560`, `0x140679590`, `0x14067ae80`) resolve to `0x143d6d988`.
///
/// It is spelled at the 1.17 address because the constants it produces are compared against the
/// bytes of the running game. See [`Image::EldenRing1170`] for why a mapped RVA does not make a
/// 1.16.2 signature usable.
const GAME_MAN_SINGLETON_VA: u64 = 0x143d6d988;
/// `GameMan+0xb72` / `+0xb73`, the two save-request flags the retractions clear.
const GAME_MAN_SAVE_REQUEST_B72_OFFSET: i64 = 0xb72;
const GAME_MAN_SAVE_REQUEST_B73_OFFSET: i64 = 0xb73;
/// `CS::MenuJob::EmitResult`.
const MENU_JOB_EMIT_RESULT_VA: u64 = 0x140746e80;
/// The game's own retractions of the two save-request flags, at their 1.17 addresses.
///
/// 1.16.2 `FUN_140678740` / `FUN_140678710`; 1.17 `0x140679590` / `0x140679560`, both `+0xe50`,
/// which is the whole-region delta for this part of `.text` and agrees with three unanimous
/// caller votes each. Read out of `eldenring-deobf-1.17.bin`, the two bodies are still exactly
/// `mov rax,[rip+disp]; mov byte [rax+0xb7x],0; ret`, and the field offsets `+0xb72`/`+0xb73` are
/// unchanged -- only `disp` moved, because the singleton did.
///
/// The site address matters as much as the target: a RIP displacement is the distance between
/// them, so generating the signature at the 1.16.2 site would encode the wrong four bytes even
/// with the target corrected.
const SAVE_REQUEST_RETRACT_B72_VA: u64 = 0x140679590;
const SAVE_REQUEST_RETRACT_B73_VA: u64 = 0x140679560;
/// The `CS::MessageBoxBuilder` recipe, lifted from the native Yes/No confirm `FUN_1407b73d0`.
const MSGBOX_BUILDER_CTOR_VA: u64 = 0x1407af730;
const MSGBOX_ADD_YES_VA: u64 = 0x1407b1c70;
const MSGBOX_ADD_NO_VA: u64 = 0x1407b1900;
const MSGBOX_DEFAULT_LAST_VA: u64 = 0x1407b1b60;
const MSGBOX_FINALIZE_VA: u64 = 0x1407b10f0;
const MSGBOX_DTOR_VA: u64 = 0x1407b0140;
/// `MessageBoxBuilder+0x10f0` is the button count and `+0x28` the default index; `default_last`
/// is the whole two-field body `default = count - 1`.
const MSGBOX_BUILDER_BUTTON_COUNT_OFFSET: i64 = 0x10f0;
const MSGBOX_BUILDER_DEFAULT_INDEX_OFFSET: i64 = 0x28;

/// The native profile-renderer refresh, `er_loading_portrait_core::PROFILE_RENDERER_REFRESH_RVA`
/// (1.17.1 `0x1409ab820`). See `profile_table_mid_hook`.
const PROFILE_RENDERER_REFRESH_VA: u64 = 0x1409aa680;
/// Offsets into the refresh, identical in 1.16.2 and 1.17.1 (the body is `IDENTICAL-WHOLE`).
const PROFILE_RENDERER_REFRESH_LATCH_CHECK: u64 = 0x54;
const PROFILE_RENDERER_REFRESH_NEXT_SLOT: u64 = 0xce;
/// Each window's address, named so `verify-aob-patterns-1170.py` can read the spec's `va:`.
const PROFILE_REFRESH_LATCH_CHECK_VA: u64 =
    PROFILE_RENDERER_REFRESH_VA + PROFILE_RENDERER_REFRESH_LATCH_CHECK;
const PROFILE_REFRESH_NEXT_SLOT_VA: u64 =
    PROFILE_RENDERER_REFRESH_VA + PROFILE_RENDERER_REFRESH_NEXT_SLOT;
/// `CSMenuProfModelRend+0x754` / `+0x755`, the two request latches the refresh tests per slot.
const PROFILE_RENDERER_REQUEST_LATCH_OFFSET: i64 = 0x754;
const PROFILE_RENDERER_REQUEST_PENDING_OFFSET: i64 = 0x755;
/// `+0xce` advances `RSI` one table entry and stops after this many slots.
const PROFILE_TABLE_ENTRY_STRIDE: i32 = 8;
const PROFILE_TABLE_SLOT_COUNT: i32 = 10;

/// `+0x54`: `CMP byte [RDI+0x754],0; JNE +0xce; CMP byte [RDI+0x755],0` -- the first read of
/// `table[slot]` and the 16 bytes the mid hook moves.
fn profile_refresh_latch_check(asm: &mut CodeAssembler) -> Result<(), iced_x86::IcedError> {
    asm.cmp(byte_ptr(rdi + PROFILE_RENDERER_REQUEST_LATCH_OFFSET), 0)?;
    asm.jne(PROFILE_REFRESH_NEXT_SLOT_VA)?;
    asm.cmp(byte_ptr(rdi + PROFILE_RENDERER_REQUEST_PENDING_OFFSET), 0)?;
    Ok(())
}

/// `+0xce`: `INC EBX; ADD RSI,8; CMP EBX,10`, the loop's own skip-this-slot join.
fn profile_refresh_next_slot(asm: &mut CodeAssembler) -> Result<(), iced_x86::IcedError> {
    asm.inc(ebx)?;
    asm.add(rsi, PROFILE_TABLE_ENTRY_STRIDE)?;
    asm.cmp(ebx, PROFILE_TABLE_SLOT_COUNT)?;
    Ok(())
}

fn main() {
    prologue_build::declare_rerun(SUPPORT);

    generate(
        &[
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_JOB_CTOR_SIG",
                    doc: "`mov [rsp+8],rcx; push rbx/rbp/rsi/rdi/r14; sub rsp,0x30`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_JOB_CTOR_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x89, 0x4c, 0x24, 0x08, 0x53, 0x55, 0x56, 0x57, 0x41, 0x56, 0x48,
                        0x83, 0xec, 0x30,
                    ],
                },
                (|asm| {
                    asm.mov(qword_ptr(rsp + 8), rcx)?;
                    asm.push(rbx)?;
                    asm.push(rbp)?;
                    asm.push(rsi)?;
                    asm.push(rdi)?;
                    asm.push(r14)?;
                    asm.sub(rsp, 0x30)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_RESULT_GATE_SIG",
                    doc: "`mov [rsp+0x18],r8; push rbp/rsi/rdi; sub rsp,0x40`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_RESULT_GATE_VA,
                    take: 0,
                    pin: &[
                        0x4c, 0x89, 0x44, 0x24, 0x18, 0x55, 0x56, 0x57, 0x48, 0x83, 0xec, 0x40,
                    ],
                },
                (|asm| {
                    asm.mov(qword_ptr(rsp + 0x18), r8)?;
                    asm.push(rbp)?;
                    asm.push(rsi)?;
                    asm.push(rdi)?;
                    asm.sub(rsp, 0x40)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_TERMINAL_CALLBACK_SIG",
                    doc: "Seven callee-saved pushes: `rbp, rsi, rdi, r12, r13, r14, r15`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_TERMINAL_CALLBACK_VA,
                    take: 0,
                    pin: &[
                        0x40, 0x55, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57,
                    ],
                },
                (|asm| {
                    rex_push(asm, rbp)?;
                    asm.push(rsi)?;
                    asm.push(rdi)?;
                    asm.push(r12)?;
                    asm.push(r13)?;
                    asm.push(r14)?;
                    asm.push(r15)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_VALIDATOR_INIT_SIG",
                    doc: "`mov [rsp+8],rcx; push rbx; sub rsp,0x30`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_VALIDATOR_INIT_VA,
                    take: 0,
                    pin: VALIDATOR_PIN,
                },
                validator_prologue as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_VALIDATOR_DTOR_SIG",
                    doc: "Byte-identical to the validator's init prologue, which is why each is\n\
                          checked at its own RVA rather than one standing in for the other.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_VALIDATOR_DTOR_VA,
                    take: 0,
                    pin: VALIDATOR_PIN,
                },
                validator_prologue as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_ENTER_NAME_SIG",
                    doc: "`push rbp/rsi/rdi; sub rsp,0x70`. The window stops inside that `sub`,\n\
                          so the assembled sequence is one byte longer than the constant.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_ENTER_NAME_VA,
                    take: SOFTWARE_KEYBOARD_ENTER_NAME_CHECKED_BYTES,
                    pin: &[0x40, 0x55, 0x56, 0x57, 0x48, 0x83, 0xec],
                },
                (|asm| {
                    rex_push(asm, rbp)?;
                    asm.push(rsi)?;
                    asm.push(rdi)?;
                    asm.sub(rsp, 0x70)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_SET_INITIAL_SIG",
                    doc: "`push rbp/rsi/rdi; lea rbp,[rsp-0x47]`.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_SET_INITIAL_VA,
                    take: 0,
                    pin: &[0x40, 0x55, 0x56, 0x57, 0x48, 0x8d, 0x6c, 0x24, 0xb9],
                },
                (|asm| {
                    rex_push(asm, rbp)?;
                    asm.push(rsi)?;
                    asm.push(rdi)?;
                    asm.lea(rbp, qword_ptr(rsp - 0x47))?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SOFTWARE_KEYBOARD_SET_MAX_SIG",
                    doc: "`mov eax,1; cmp edx,eax; cmovge eax,edx` -- the clamp that makes the\n\
                          max-length setter's floor 1.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: SOFTWARE_KEYBOARD_SET_MAX_VA,
                    take: 0,
                    pin: &[0xb8, 0x01, 0x00, 0x00, 0x00, 0x3b, 0xd0, 0x0f, 0x4d, 0xc2],
                },
                (|asm| {
                    asm.mov(eax, 1)?;
                    cmp_r32_rm32(asm, Register::EDX, Register::EAX)?;
                    asm.cmovge(eax, edx)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "GAME_HEAP_ALLOC_SIG",
                    doc: "The allocator thunk: `mov rax,[r8]; mov r9,r8; mov r8,rdx` before it\n\
                          tail-jumps through the allocator vtable.",
                    visibility: "",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: GAME_HEAP_ALLOC_VA,
                    take: 0,
                    pin: &[0x49, 0x8b, 0x00, 0x4d, 0x8b, 0xc8, 0x4c, 0x8b, 0xc2],
                },
                (|asm| {
                    mov_r64_mem_base(asm, Register::RAX, Register::R8)?;
                    mov_r64_rm64(asm, Register::R9, Register::R8)?;
                    mov_r64_rm64(asm, Register::R8, Register::RDX)?;
                    Ok(())
                }) as Assemble,
            ),
        ],
        "generated_software_keyboard_prologues.rs",
    );

    generate(
        &[
            (
                PrologueSpec {
                    name: "MENU_JOB_EMIT_RESULT_SIG",
                    doc: "Prologue of `CS::MenuJob::EmitResult`:\n\
                          `mov [rsp+0x10],rdx; push rbx; sub rsp,0x80`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: MENU_JOB_EMIT_RESULT_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x89, 0x54, 0x24, 0x10, 0x53, 0x48, 0x81, 0xec, 0x80, 0x00, 0x00,
                        0x00,
                    ],
                },
                (|asm| {
                    asm.mov(qword_ptr(rsp + 0x10), rdx)?;
                    asm.push(rbx)?;
                    asm.sub(rsp, 0x80)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SAVE_REQUEST_RETRACT_B72_SIG",
                    doc: "WHOLE BODY of the `+0xb72` retraction (1.16.2 `FUN_140678740`, 1.17\n\
                          `0x140679590`): load the GameMan singleton, store 0 into `+0xb72`,\n\
                          return. Verified before the call: if the bytes ever differ, the\n\
                          address means something else in that build and the retraction is\n\
                          skipped rather than fired blind at unknown code.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing1170,
                    va: SAVE_REQUEST_RETRACT_B72_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x8B, 0x05, 0xF1, 0x43, 0x6F, 0x03, 0xC6, 0x80, 0x72, 0x0B, 0x00,
                        0x00, 0x00, 0xC3,
                    ],
                },
                (|asm| {
                    mov_rax_rip_absolute(asm, GAME_MAN_SINGLETON_VA)?;
                    asm.mov(byte_ptr(rax + GAME_MAN_SAVE_REQUEST_B72_OFFSET), 0)?;
                    asm.ret()?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SAVE_REQUEST_RETRACT_B73_SIG",
                    doc: "WHOLE BODY of the `+0xb73` retraction (1.16.2 `FUN_140678710`, 1.17\n\
                          `0x140679560`), the same three instructions against `+0xb73`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing1170,
                    va: SAVE_REQUEST_RETRACT_B73_VA,
                    take: 0,
                    pin: &[
                        0x48, 0x8B, 0x05, 0x21, 0x44, 0x6F, 0x03, 0xC6, 0x80, 0x73, 0x0B, 0x00,
                        0x00, 0x00, 0xC3,
                    ],
                },
                (|asm| {
                    mov_rax_rip_absolute(asm, GAME_MAN_SINGLETON_VA)?;
                    asm.mov(byte_ptr(rax + GAME_MAN_SAVE_REQUEST_B73_OFFSET), 0)?;
                    asm.ret()?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SYSTEM_QUIT_MSGBOX_BUILDER_CTOR_SIG",
                    doc: "`ctor(rcx=builder, rdx=ctx, r8=prompt MenuString*, r9=&mode_i32,\n\
                          [rsp+0x28]=0u8)`: `push rbp/rsi/rdi; sub rsp,0x80`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: MSGBOX_BUILDER_CTOR_VA,
                    take: 0,
                    pin: &[
                        0x40, 0x55, 0x56, 0x57, 0x48, 0x81, 0xec, 0x80, 0x00, 0x00, 0x00,
                    ],
                },
                (|asm| {
                    rex_push(asm, rbp)?;
                    asm.push(rsi)?;
                    asm.push(rdi)?;
                    asm.sub(rsp, 0x80)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SYSTEM_QUIT_MSGBOX_ADD_YES_SIG",
                    doc: "`add_yes(rcx=builder, rdx=&SaveFlowYesButtonDesc) -> builder`:\n\
                          `mov r11,rsp; push rdi; sub rsp,0x90`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: MSGBOX_ADD_YES_VA,
                    take: 0,
                    pin: &[
                        0x4c, 0x8b, 0xdc, 0x57, 0x48, 0x81, 0xec, 0x90, 0x00, 0x00, 0x00,
                    ],
                },
                (|asm| {
                    mov_r64_rm64(asm, Register::R11, Register::RSP)?;
                    asm.push(rdi)?;
                    asm.sub(rsp, 0x90)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SYSTEM_QUIT_MSGBOX_ADD_NO_SIG",
                    doc: "`add_no(rcx=builder) -> builder`: `push rdi; sub rsp,0xa0`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: MSGBOX_ADD_NO_VA,
                    take: 0,
                    pin: &[0x40, 0x57, 0x48, 0x81, 0xec, 0xa0, 0x00, 0x00, 0x00],
                },
                (|asm| {
                    rex_push(asm, rdi)?;
                    asm.sub(rsp, 0xa0)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SYSTEM_QUIT_MSGBOX_DEFAULT_LAST_SIG",
                    doc: "WHOLE BODY of `default_last(rcx=builder) -> builder`:\n\
                          `builder->default_index = builder->button_count - 1; return builder;`.\n\
                          That is why add order encodes the default.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: MSGBOX_DEFAULT_LAST_VA,
                    take: 0,
                    pin: &[
                        0x8b, 0x81, 0xf0, 0x10, 0x00, 0x00, 0xff, 0xc8, 0x89, 0x41, 0x28, 0x48,
                        0x8b, 0xc1, 0xc3,
                    ],
                },
                (|asm| {
                    mov_r32_mem(
                        asm,
                        Register::EAX,
                        Register::RCX,
                        MSGBOX_BUILDER_BUTTON_COUNT_OFFSET,
                    )?;
                    asm.dec(eax)?;
                    asm.mov(dword_ptr(rcx + MSGBOX_BUILDER_DEFAULT_INDEX_OFFSET), eax)?;
                    mov_r64_rm64(asm, Register::RAX, Register::RCX)?;
                    asm.ret()?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SYSTEM_QUIT_MSGBOX_FINALIZE_SIG",
                    doc: "`finalize(rcx=builder, rdx=&job_slot, r8b=0) -> &job_slot`:\n\
                          `mov r11,rsp; push rsi/rdi/r14; sub rsp,0x130`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: MSGBOX_FINALIZE_VA,
                    take: 0,
                    pin: &[
                        0x4c, 0x8b, 0xdc, 0x56, 0x57, 0x41, 0x56, 0x48, 0x81, 0xec, 0x30, 0x01,
                        0x00, 0x00,
                    ],
                },
                (|asm| {
                    mov_r64_rm64(asm, Register::R11, Register::RSP)?;
                    asm.push(rsi)?;
                    asm.push(rdi)?;
                    asm.push(r14)?;
                    asm.sub(rsp, 0x130)?;
                    Ok(())
                }) as Assemble,
            ),
            (
                PrologueSpec {
                    name: "SYSTEM_QUIT_MSGBOX_DTOR_SIG",
                    doc: "`dtor(rcx=builder)`: `mov [rsp+8],rcx; push rdi; sub rsp,0x30`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: MSGBOX_DTOR_VA,
                    take: 0,
                    pin: &[0x48, 0x89, 0x4c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x30],
                },
                (|asm| {
                    asm.mov(qword_ptr(rsp + 8), rcx)?;
                    asm.push(rdi)?;
                    asm.sub(rsp, 0x30)?;
                    Ok(())
                }) as Assemble,
            ),
        ],
        "generated_save_flow_prologues.rs",
    );

    // The profile-renderer refresh reads `table[slot]+0x754` without testing the slot. The mid
    // hook overwrites the three instructions starting at that read (ilhook writes up to 14 bytes),
    // and the window is those three in full, so the bytes it moves are exactly the bytes checked.
    generate(
        &[
            (
                PrologueSpec {
                    name: "PROFILE_REFRESH_LATCH_CHECK_WINDOW",
                    doc: "1.16.2 profile-renderer refresh `+0x54`, the first read of `table[slot]`:\n\
                          `CMP byte [RDI+0x754],0; JNE +0xce; CMP byte [RDI+0x755],0`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: PROFILE_REFRESH_LATCH_CHECK_VA,
                    take: 0,
                    pin: &[
                        0x80, 0xbf, 0x54, 0x07, 0x00, 0x00, 0x00, 0x75, 0x71, 0x80, 0xbf, 0x55,
                        0x07, 0x00, 0x00, 0x00,
                    ],
                },
                profile_refresh_latch_check as Assemble,
            ),
            (
                PrologueSpec {
                    name: "PROFILE_REFRESH_NEXT_SLOT_WINDOW",
                    doc: "1.16.2 profile-renderer refresh `+0xce`, the loop's skip-this-slot join:\n\
                          `INC EBX; ADD RSI,8; CMP EBX,10`.",
                    visibility: "pub(crate)",
                    shape: Shape::Slice,
                    image: Image::EldenRing,
                    va: PROFILE_REFRESH_NEXT_SLOT_VA,
                    take: 0,
                    pin: &[0xff, 0xc3, 0x48, 0x83, 0xc6, 0x08, 0x83, 0xfb, 0x0a],
                },
                profile_refresh_next_slot as Assemble,
            ),
        ],
        "generated_profile_table_mid_hook_windows.rs",
    );
}
