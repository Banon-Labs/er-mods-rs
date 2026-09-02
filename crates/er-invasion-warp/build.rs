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

// ---------------------------------------------------------------------------------------------
// Seamless Co-op's `ersc.dll`, at its preferred base `0x180000000`
//
// TWO builds are described here, v1.9.9 and v2.0.0, because `ersc.dll` is third-party: the user
// installs and updates it on their own schedule and may downgrade, and both builds are present on
// a machine that has updated once (the launcher leaves the previous one in `_SeamlessCoop/`).
// `local_invasion_filter.rs` picks between the two AT RUNTIME by byte-checking the invade action,
// and refuses on a build that matches neither -- see `ersc::Abi` there.
//
// # These pins are longer than a prologue, on purpose
//
// The two option actions open with fourteen IDENTICAL bytes, and in v2.0.0 five different actions
// share them. A fourteen-byte check therefore proves "an option action lives here", not "THE
// invade action lives here" -- and this module's failure mode is calling the wrong one and
// cancelling other players' invasions. So each option-action pin runs through the state WRITE,
// which is the instruction that makes an action what it is. What that covers, all in one check:
//
//   the session load offset        `mov rdi,[rcx+0x58]`
//   the state field and its idle   `cmp dword [rdi+STATE], IDLE`
//   the mutex sub-object offset    `lea rsi,[rdi+MUTEX]`
//   the mutex lock/unlock pair     `call <lock>` (its rel32 is a fixed constant of the build)
//   the guard field and its poison `cmp dword [rdi+GUARD], 0x7fffffff`
//   the action code                `mov dword [rdi+STATE], CODE`
//
// Measured 2026-09-02: every pin below occurs EXACTLY ONCE in the build it describes and NOT AT
// ALL in the other build, which is what makes the runtime version gate sound rather than a guess
// (`uv run --with capstone python3 scripts/ersc-disas.py crossmatch <va> --build <v199|v200> -n <len>`).
// `show` is the one exception and is not used as the discriminator: it is byte-identical in both
// builds because it is literally the same code at a different address -- and it is also the one
// function this module HOOKS, so its bytes stop being the shipped bytes once the detour is in.

/// v1.9.9 entry points, measured 2026-08-05.
const ERSC199_SHOW_VA: u64 = 0x180022d30;
const ERSC199_INVADE_ACTION_VA: u64 = 0x1800243e0;
const ERSC199_CANCEL_ACTION_VA: u64 = 0x180024460;
const ERSC199_BUILD_LOBBY_KEY_VA: u64 = 0x1800abc20;
/// The `std::mutex`-ish lock both option actions take on the session sub-object, and the unlock
/// they tail-jump to. Named because the pins encode the `call`'s rel32 to it.
const ERSC199_SESSION_LOCK_VA: u64 = 0x1800f4868;

/// v2.0.0 entry points, measured 2026-09-02. See `local_invasion_filter.rs` for the evidence that
/// identifies each one; none of them was taken from a prologue match alone.
const ERSC200_SHOW_VA: u64 = 0x1800241a0;
const ERSC200_INVADE_ACTION_VA: u64 = 0x180025850;
const ERSC200_CANCEL_ACTION_VA: u64 = 0x1800258d0;
const ERSC200_BUILD_LOBBY_KEY_VA: u64 = 0x1800ad590;
const ERSC200_SESSION_LOCK_VA: u64 = 0x1800f96d8;

/// `OSM+0x58`, the session object every option action loads first. UNCHANGED across the update:
/// all five v2.0.0 actions still open `mov rdi,[rcx+0x58]`.
const ERSC_NEXT_OBJECT_OFFSET: i64 = 0x58;
/// The value both builds' guard field refuses to proceed past.
const ERSC_SESSION_GUARD_POISON: i32 = 0x7fff_ffff;

/// The session field group, which moved as a BLOCK: v2.0.0 shifted the mutex sub-object, the
/// guard and the state by exactly `+0x40` and kept their relative spacing (`guard = mutex+0x4c`,
/// `state = mutex+0x50`) identical. That is one measurement with three consequences, not three
/// independent ones.
const ERSC199_SESSION_MUTEX_OFFSET: i64 = 0xc0;
const ERSC199_SESSION_GUARD_OFFSET: i64 = 0x10c;
const ERSC199_SESSION_STATE_OFFSET: i64 = 0x110;
const ERSC200_SESSION_MUTEX_OFFSET: i64 = 0x100;
const ERSC200_SESSION_GUARD_OFFSET: i64 = 0x14c;
const ERSC200_SESSION_STATE_OFFSET: i64 = 0x150;

/// The session-state enum, which v2.0.0 renumbered by `+1` throughout. Proven exhaustively rather
/// than inferred from the two actions: a scan of every `mov dword [reg+STATE], imm32` in each
/// build's real code finds SEVEN distinct values whose site counts match one-for-one under a
/// uniform `+1` (`0x1`x1, `0x3`x1, `0x6`x1, `0x9`x1, `0xd`x1, `0x22`x7, `0x23`x1 becoming `0x2`,
/// `0x4`, `0x7`, `0xa`, `0xe`, `0x23`x7, `0x24`). Reproduce with
/// `scripts/ersc-disas.py states 0x110 --build v199` and `... states 0x150 --build v200`.
const ERSC199_STATE_IDLE: i32 = 0x00;
const ERSC199_STATE_SEARCHING: i32 = 0x0d;
const ERSC199_STATE_CANCELLING: i32 = 0x22;
const ERSC200_STATE_IDLE: i32 = 0x01;
const ERSC200_STATE_SEARCHING: i32 = 0x0e;
const ERSC200_STATE_CANCELLING: i32 = 0x23;

/// `BuildLobbyKey`'s first act is to check a pointer field on its `ctx` argument. That field moved
/// with the rest of the object.
const ERSC199_LOBBY_KEY_CTX_OFFSET: i64 = 0xb8;
const ERSC200_LOBBY_KEY_CTX_OFFSET: i64 = 0xc8;

/// Where the invade action's shared tail begins, relative to the function entry. v1.9.9 branches
/// FORWARD to it over an inline early return; v2.0.0 falls straight into it and branches forward
/// to a return block placed after the state write instead ([`ERSC200_INVADE_RETURN`]).
const ERSC199_INVADE_TAIL: u64 = 0x1e;
/// v2.0.0's relocated early-return block, the target of the inverted idle guard.
const ERSC200_INVADE_RETURN: u64 = 0x4e;

/// The four option actions this repo pins, with every measured number in one place. `fatal_5` and
/// `fatal_6` are byte offsets from the function entry to the two error blocks the tail branches
/// to; both sit past the end of the pinned window, so they are named as offsets read off the
/// disassembly rather than as encoded displacements.
const ERSC199_INVADE: ErscAction = ErscAction {
    va: ERSC199_INVADE_ACTION_VA,
    lock_va: ERSC199_SESSION_LOCK_VA,
    mutex: ERSC199_SESSION_MUTEX_OFFSET,
    guard: ERSC199_SESSION_GUARD_OFFSET,
    state: ERSC199_SESSION_STATE_OFFSET,
    code: ERSC199_STATE_SEARCHING,
    fatal_5: 0x55,
    fatal_6: 0x5f,
};
const ERSC200_INVADE: ErscAction = ErscAction {
    va: ERSC200_INVADE_ACTION_VA,
    lock_va: ERSC200_SESSION_LOCK_VA,
    mutex: ERSC200_SESSION_MUTEX_OFFSET,
    guard: ERSC200_SESSION_GUARD_OFFSET,
    state: ERSC200_SESSION_STATE_OFFSET,
    code: ERSC200_STATE_SEARCHING,
    // Eight bytes further than v1.9.9's, which is exactly the relocated return block above.
    fatal_5: 0x56,
    fatal_6: 0x60,
};
const ERSC199_CANCEL: ErscAction = ErscAction {
    va: ERSC199_CANCEL_ACTION_VA,
    lock_va: ERSC199_SESSION_LOCK_VA,
    mutex: ERSC199_SESSION_MUTEX_OFFSET,
    guard: ERSC199_SESSION_GUARD_OFFSET,
    state: ERSC199_SESSION_STATE_OFFSET,
    code: ERSC199_STATE_CANCELLING,
    fatal_5: 0x45,
    fatal_6: 0x4f,
};
const ERSC200_CANCEL: ErscAction = ErscAction {
    va: ERSC200_CANCEL_ACTION_VA,
    lock_va: ERSC200_SESSION_LOCK_VA,
    mutex: ERSC200_SESSION_MUTEX_OFFSET,
    guard: ERSC200_SESSION_GUARD_OFFSET,
    state: ERSC200_SESSION_STATE_OFFSET,
    code: ERSC200_STATE_CANCELLING,
    fatal_5: 0x45,
    fatal_6: 0x4f,
};

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

/// The shared opening of every ERSC option action: CET landing pad, two pushes, shadow space,
/// then the session load. Identical in both builds and shared by five functions within v2.0.0,
/// which is exactly why no pin stops here.
fn ersc_option_action_opening(asm: &mut CodeAssembler) -> Result<(), iced_x86::IcedError> {
    asm.endbr64()?;
    asm.push(rsi)?;
    asm.push(rdi)?;
    asm.sub(rsp, 0x28)?;
    mov_r64_mem(asm, Register::RDI, Register::RCX, ERSC_NEXT_OBJECT_OFFSET)
}

/// Everything one option action needs said about it, so the four bodies below differ only in
/// their measured numbers rather than in their code.
///
/// The three branch targets are given as OFFSETS from the function entry because that is how they
/// were read off the disassembly, and because two of them sit past the end of the pinned window --
/// naming the absolute address instead would mean typing an encoded displacement, which is the
/// hand-typed machine code this whole generator exists to avoid.
struct ErscAction {
    /// Function entry, so a branch target can be named as the address it actually is.
    va: u64,
    lock_va: u64,
    mutex: i64,
    guard: i64,
    state: i64,
    /// The value this action writes to the state field. What the action IS.
    code: i32,
    /// `mov ecx,5; call <fatal>` -- taken when the mutex is already held.
    fatal_5: u64,
    /// `mov [rdi+guard],0x7ffffffe; mov ecx,6; call <fatal>` -- taken when the guard is poisoned.
    fatal_6: u64,
}

/// The tail every option action shares: take the session mutex, bail if the guard is poisoned,
/// then write the action's code into the state field.
///
/// Nothing here is masked, so the two `jcc` displacements and the `call`'s rel32 are all pinned
/// exactly. They are constants of the build's own layout rather than relocations, and pinning
/// them is what turns "an option action is here" into "THIS option action is here".
fn ersc_option_action_tail(
    asm: &mut CodeAssembler,
    action: &ErscAction,
) -> Result<(), iced_x86::IcedError> {
    asm.lea(rsi, qword_ptr(rdi + action.mutex))?;
    asm.mov(rcx, rsi)?;
    asm.call(action.lock_va)?;
    asm.test(eax, eax)?;
    asm.jne(action.va + action.fatal_5)?;
    asm.cmp(dword_ptr(rdi + action.guard), ERSC_SESSION_GUARD_POISON)?;
    asm.je(action.va + action.fatal_6)?;
    asm.mov(dword_ptr(rdi + action.state), action.code)
}

/// A CANCEL action: no state precondition at all. It is the only unguarded shape in either build,
/// which is what made it the one entry point a masked BODY search could still find in v2.0.0.
fn ersc_cancel_action(
    asm: &mut CodeAssembler,
    action: &ErscAction,
) -> Result<(), iced_x86::IcedError> {
    ersc_option_action_opening(asm)?;
    ersc_option_action_tail(asm, action)
}

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
                    name: "V199_SHOW_PROLOGUE",
                    doc: "v1.9.9 `show(void* OSM, int groupId)`: eight callee-saved pushes and a\n\
                          0x188 frame. NOT the version discriminator, for two separate reasons --\n\
                          `show` is byte-identical in v2.0.0 (it is the same code at a different\n\
                          address), and this module HOOKS it, so after installation the bytes\n\
                          here are our own detour rather than Seamless's.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc199,
                    va: ERSC199_SHOW_VA,
                    take: 0,
                    pin: &[
                        0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
                        0x48, 0x81, 0xec, 0x88, 0x01, 0x00, 0x00,
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
                    name: "V200_SHOW_PROLOGUE",
                    doc: "v2.0.0 `show`. Identical bytes to [`V199_SHOW_PROLOGUE`] at a different\n\
                          address: v2.0.0 recompiled this function to the byte and moved it from\n\
                          `0x22d30` to `0x241a0`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc200,
                    va: ERSC200_SHOW_VA,
                    take: 0,
                    pin: &[
                        0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
                        0x48, 0x81, 0xec, 0x88, 0x01, 0x00, 0x00,
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
                    name: "V199_INVADE_PROLOGUE",
                    doc: "v1.9.9 \"Invade world\", through the state write. Runs from the option\n\
                          action opening, past `cmp [rdi+0x110],0` and its `je` over the inline\n\
                          early return, into the shared mutex/guard tail, and ends on\n\
                          `mov [rdi+0x110],0xd` -- the ONE site in the whole unpacked `.text`\n\
                          that puts that value in that field. This is the version discriminator:\n\
                          it occurs exactly once in v1.9.9 and nowhere at all in v2.0.0, and\n\
                          unlike `show` it is called but never hooked, so its bytes stay the\n\
                          shipped bytes for the life of the process.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc199,
                    va: ERSC199_INVADE_ACTION_VA,
                    take: 0,
                    pin: &[
                        0xf3, 0x0f, 0x1e, 0xfa, 0x56, 0x57, 0x48, 0x83, 0xec, 0x28, 0x48, 0x8b,
                        0x79, 0x58, 0x83, 0xbf, 0x10, 0x01, 0x00, 0x00, 0x00, 0x74, 0x07, 0x48,
                        0x83, 0xc4, 0x28, 0x5f, 0x5e, 0xc3, 0x48, 0x8d, 0xb7, 0xc0, 0x00, 0x00,
                        0x00, 0x48, 0x89, 0xf1, 0xe8, 0x5b, 0x04, 0x0d, 0x00, 0x85, 0xc0, 0x75,
                        0x24, 0x81, 0xbf, 0x0c, 0x01, 0x00, 0x00, 0xff, 0xff, 0xff, 0x7f, 0x74,
                        0x22, 0xc7, 0x87, 0x10, 0x01, 0x00, 0x00, 0x0d, 0x00, 0x00, 0x00,
                    ],
                },
                (|asm| {
                    ersc_option_action_opening(asm)?;
                    // v1.9.9 shape: proceed only when the session is idle, with the early return
                    // INLINE right after the branch. v2.0.0 inverted this (see below), which is
                    // exactly why a masked body search found no match for this function there.
                    asm.cmp(
                        dword_ptr(rdi + ERSC199_SESSION_STATE_OFFSET),
                        ERSC199_STATE_IDLE,
                    )?;
                    asm.je(ERSC199_INVADE_ACTION_VA + ERSC199_INVADE_TAIL)?;
                    asm.add(rsp, 0x28)?;
                    asm.pop(rdi)?;
                    asm.pop(rsi)?;
                    asm.ret()?;
                    ersc_option_action_tail(asm, &ERSC199_INVADE)
                }) as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "V200_INVADE_PROLOGUE",
                    doc: "v2.0.0 \"Invade world\" at `0x25850`, through `mov [rdi+0x150],0xe`.\n\
                          The counterpart of [`V199_INVADE_PROLOGUE`], and the v2.0.0 half of the\n\
                          version discriminator. The compiler INVERTED the idle guard here --\n\
                          `cmp [rdi+0x150],1; jne <return block moved to the end>` where v1.9.9\n\
                          had `cmp [rdi+0x110],0; je <continue>` -- and `je`/`jne` differ in the\n\
                          OPCODE byte, which a masked search keeps. That one byte at offset 21 is\n\
                          why locating this function by body signature reported no match at any\n\
                          length while the cancel action beside it mapped cleanly.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc200,
                    va: ERSC200_INVADE_ACTION_VA,
                    take: 0,
                    pin: &[
                        0xf3, 0x0f, 0x1e, 0xfa, 0x56, 0x57, 0x48, 0x83, 0xec, 0x28, 0x48, 0x8b,
                        0x79, 0x58, 0x83, 0xbf, 0x50, 0x01, 0x00, 0x00, 0x01, 0x75, 0x37, 0x48,
                        0x8d, 0xb7, 0x00, 0x01, 0x00, 0x00, 0x48, 0x89, 0xf1, 0xe8, 0x62, 0x3e,
                        0x0d, 0x00, 0x85, 0xc0, 0x75, 0x2c, 0x81, 0xbf, 0x4c, 0x01, 0x00, 0x00,
                        0xff, 0xff, 0xff, 0x7f, 0x74, 0x2a, 0xc7, 0x87, 0x50, 0x01, 0x00, 0x00,
                        0x0e, 0x00, 0x00, 0x00,
                    ],
                },
                (|asm| {
                    ersc_option_action_opening(asm)?;
                    asm.cmp(
                        dword_ptr(rdi + ERSC200_SESSION_STATE_OFFSET),
                        ERSC200_STATE_IDLE,
                    )?;
                    asm.jne(ERSC200_INVADE_ACTION_VA + ERSC200_INVADE_RETURN)?;
                    ersc_option_action_tail(asm, &ERSC200_INVADE)
                }) as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "V199_CANCEL_PROLOGUE",
                    doc: "v1.9.9 \"Cancel search\", through `mov [rdi+0x110],0x22`. No state\n\
                          precondition -- the only unguarded option action in the build, and the\n\
                          reason a masked body search could still find this one in v2.0.0.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc199,
                    va: ERSC199_CANCEL_ACTION_VA,
                    take: 0,
                    pin: &[
                        0xf3, 0x0f, 0x1e, 0xfa, 0x56, 0x57, 0x48, 0x83, 0xec, 0x28, 0x48, 0x8b,
                        0x79, 0x58, 0x48, 0x8d, 0xb7, 0xc0, 0x00, 0x00, 0x00, 0x48, 0x89, 0xf1,
                        0xe8, 0xeb, 0x03, 0x0d, 0x00, 0x85, 0xc0, 0x75, 0x24, 0x81, 0xbf, 0x0c,
                        0x01, 0x00, 0x00, 0xff, 0xff, 0xff, 0x7f, 0x74, 0x22, 0xc7, 0x87, 0x10,
                        0x01, 0x00, 0x00, 0x22, 0x00, 0x00, 0x00,
                    ],
                },
                (|asm| ersc_cancel_action(asm, &ERSC199_CANCEL)) as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "V200_CANCEL_PROLOGUE",
                    doc: "v2.0.0 \"Cancel search\" at `0x258d0`, through `mov [rdi+0x150],0x23`.\n\
                          Byte-for-byte the same shape as [`V199_CANCEL_PROLOGUE`] with the\n\
                          session field group moved `+0x40` and the state code renumbered `+1`.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc200,
                    va: ERSC200_CANCEL_ACTION_VA,
                    take: 0,
                    pin: &[
                        0xf3, 0x0f, 0x1e, 0xfa, 0x56, 0x57, 0x48, 0x83, 0xec, 0x28, 0x48, 0x8b,
                        0x79, 0x58, 0x48, 0x8d, 0xb7, 0x00, 0x01, 0x00, 0x00, 0x48, 0x89, 0xf1,
                        0xe8, 0xeb, 0x3d, 0x0d, 0x00, 0x85, 0xc0, 0x75, 0x24, 0x81, 0xbf, 0x4c,
                        0x01, 0x00, 0x00, 0xff, 0xff, 0xff, 0x7f, 0x74, 0x22, 0xc7, 0x87, 0x50,
                        0x01, 0x00, 0x00, 0x23, 0x00, 0x00, 0x00,
                    ],
                },
                (|asm| ersc_cancel_action(asm, &ERSC200_CANCEL)) as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "V199_BUILD_LOBBY_KEY_PROLOGUE",
                    doc: "v1.9.9 `BuildLobbyKey(ctx, std::string* out)`, through the first thing\n\
                          it examines. The eight pushes, a 0x148 frame, the xmm6 spill and stack\n\
                          cookie, `mov r15,rdx` saving the out-param, then\n\
                          `cmp qword [rcx+0xb8],0` -- the ctx field that has to be non-null.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc199,
                    va: ERSC199_BUILD_LOBBY_KEY_VA,
                    take: 0,
                    pin: &[
                        0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
                        0x48, 0x81, 0xec, 0x48, 0x01, 0x00, 0x00, 0x48, 0x8d, 0xac, 0x24, 0x80,
                        0x00, 0x00, 0x00, 0x0f, 0x29, 0xb5, 0xb0, 0x00, 0x00, 0x00, 0x48, 0xc7,
                        0x85, 0xa8, 0x00, 0x00, 0x00, 0xfe, 0xff, 0xff, 0xff, 0x49, 0x89, 0xd7,
                        0x48, 0x83, 0xb9, 0xb8, 0x00, 0x00, 0x00, 0x00,
                    ],
                },
                (|asm| {
                    ersc_callee_saved_pushes(asm)?;
                    asm.sub(rsp, 0x148)?;
                    asm.lea(rbp, qword_ptr(rsp + 0x80))?;
                    asm.movaps(xmmword_ptr(rbp + 0xb0), xmm6)?;
                    asm.mov(qword_ptr(rbp + 0xa8), -2)?;
                    asm.mov(r15, rdx)?;
                    asm.cmp(qword_ptr(rcx + ERSC199_LOBBY_KEY_CTX_OFFSET), 0)?;
                    Ok(())
                }) as prologue_build::Assemble,
            ),
            (
                PrologueSpec {
                    name: "V200_BUILD_LOBBY_KEY_PROLOGUE",
                    doc: "v2.0.0 `BuildLobbyKey` at `0xad590`. Instruction for instruction the\n\
                          same function as [`V199_BUILD_LOBBY_KEY_PROLOGUE`]: the frame shrank\n\
                          `0x148`->`0x108`, the out-param moved from `r15` to `rsi`, and the ctx\n\
                          field moved `+0xb8`->`+0xc8`. Its frame size is why the 19-byte v1.9.9\n\
                          prologue matched a DIFFERENT function (`0x1a6d0`, which also frames\n\
                          `0x148`) and missed this one -- the cautionary case for pinning\n\
                          anything from a unique prologue hit.",
                    visibility: "pub",
                    shape: Shape::Slice,
                    image: Image::Ersc200,
                    va: ERSC200_BUILD_LOBBY_KEY_VA,
                    take: 0,
                    pin: &[
                        0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x53,
                        0x48, 0x81, 0xec, 0x08, 0x01, 0x00, 0x00, 0x48, 0x8d, 0xac, 0x24, 0x80,
                        0x00, 0x00, 0x00, 0x0f, 0x29, 0x75, 0x70, 0x48, 0xc7, 0x45, 0x68, 0xfe,
                        0xff, 0xff, 0xff, 0x48, 0x89, 0xd6, 0x48, 0x83, 0xb9, 0xc8, 0x00, 0x00,
                        0x00, 0x00,
                    ],
                },
                (|asm| {
                    ersc_callee_saved_pushes(asm)?;
                    asm.sub(rsp, 0x108)?;
                    asm.lea(rbp, qword_ptr(rsp + 0x80))?;
                    asm.movaps(xmmword_ptr(rbp + 0x70), xmm6)?;
                    asm.mov(qword_ptr(rbp + 0x68), -2)?;
                    asm.mov(rsi, rdx)?;
                    asm.cmp(qword_ptr(rcx + ERSC200_LOBBY_KEY_CTX_OFFSET), 0)?;
                    Ok(())
                }) as prologue_build::Assemble,
            ),
        ],
        "generated_ersc_prologues.rs",
    );
}
