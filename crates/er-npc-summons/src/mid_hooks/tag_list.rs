//! The companions' overhead tags: a friendly name and HP plate, a friendly name colour, and no
//! red enemy damage bar.
//!
//! The Mimic Tear companions are built from enemy `NpcParam` rows and are not party members, so
//! the game gives them nothing a summoned friend gets. Prototyped and proven live in
//! `scripts/frida/spawn-npc.js` (`tagHooks`); static RE in bd
//! `npc-summons-in-friendly-tag-list-via-producer-probes-1171-2026-10-06`,
//! `npc-summon-tag-name-colour-is-rolenamecolor-1171-2026-10-06` and
//! `npc-summon-enemy-damage-tag-producer-1171-2026-10-06`. Nothing here writes game state outside
//! the two functions' own registers and stack locals: no party entry is added, no character field
//! changes.
//!
//! # The friendly tag list
//!
//! `FUN_140777380` (1.17.1 `0x140778200`) rebuilds the seven `ChrFriendlyTagEntry` rows at
//! `CSFeManImp+0x5c60` every frame. For slot `i` (`R15D`, reloaded from `[rsp+0x3c]` each turn of
//! the loop) it tests `0 <= i < count`, where `count` is `PartyMemberInfo+0x14` stored at
//! `[rsp+0x50]`; a member slot asks `GetFieldInsHandleByMemberIndex(pmi, [rbp-0x78], i)` for a
//! handle, resolves it with `GetChrInsFromHandle`, and fills the row from that character, its name
//! through `GetChrName` (which [`crate::names`] already answers for companions).
//!
//! * **`+0x2f8`, the slot test** ([`TAG_SLOT`]). For `count <= i < count + n`, with `n` the live
//!   companions, the routine writes companion `i - count`'s `FieldInsHandle` into `[rbp-0x78]`
//!   and resumes at `+0x31c`, the instruction after the handle call. That is exactly what the
//!   member path does for a party slot: the call it skips is a leaf
//!   (`mov qword [rdx],-1; cmp r8d,[rcx+0x14]; jge; ...; mov [rdx],rax; mov rax,rdx; ret`) that
//!   writes only `[rdx]` and returns `rdx`, so the routine sets `RAX` to the same address.
//!   `RCX`, `RDX` and `R8` are reloaded before they are read again (`+0x31c`, `+0x356`), and the
//!   flags are rewritten by the `test rcx,rcx` at `+0x323`. Every other slot runs the moved test
//!   unchanged. This is the Frida probe pair (raise the count at `+0x92`, write the handle at
//!   `+0x31c`) folded into one decision at the one place the count is read: `[rsp+0x50]` has no
//!   other reader in the function.
//! * **`+0x607`, the name colour** ([`TAG_COLOUR`]). The colour is `RoleParam.roleNameColor` of
//!   row `vowType*10000 + chrType`, and a companion is `chrType` 5, whose row says 2 (hostile).
//!   `GetRoleNameColor` returned in `AL` and `mov [rsp+0x33],al` is the first moved instruction,
//!   so for a companion (`R15` is the slot's `ChrIns*` from `+0x35f`) the routine sets `AL` to 1
//!   and the store writes that.
//!
//! The voice-chat lookup the producer's per-player helper calls reads `PlayerIns+0x6b8` without a
//! null check once past its own `test r8,r8`; a companion has no session entry there, so a MinHook
//! entry detour hands it `R8 = NULL` for companions ([`install_voice_chat_guard`]).
//!
//! # The enemy damage bar
//!
//! `CSChrDamageModule::UpdateUI` (1.17.1 `0x1404497e0`) sends a hit character to the enemy tag
//! list at `CSFeManImp+0x59f0` through `SetEntityHealthBarDamage` (`call` at `+0x363`, then
//! `jmp +0x375`). The selection starts at `+0x26e` ([`ENEMY_BAR`]); `RDI` is the module, its
//! owner at `[rdi+8]`. For a companion the routine resumes at `+0x375`, the tail that call and
//! every no-bar branch share (`+0x119`, `+0x25f`, `+0x268`, `+0x273` and `+0x368` jump there),
//! so the hit is processed as for any character that gets no bar. The state is exactly what the
//! moved `jne` at `+0x273` would carry there when it is taken: the only instruction skipped is a
//! `cmp`, and `+0x375` begins with `test bpl,bpl`, which rewrites the flags.
//!
//! # Windows, relocation, branches
//!
//! Read on 2026-10-19 in `eldenring-deobf.bin` (1.16.2), `eldenring-deobf-1.17.bin` and
//! `eldenring-deobf-1.17.1.bin`; both functions are `IDENTICAL-WHOLE` with equal `.pdata`
//! extents, and every window is byte-identical in the three images except the resume pin at
//! `+0x31c`, whose RIP displacement is masked.
//!
//! | hook | window | bytes | relocated |
//! |---|---|---|---|
//! | slot | `test r15d,r15d; js +0xa7a; cmp r15d,[rsp+0x50]` | 14 | `js rel32` |
//! | colour | `mov [rsp+0x33],al; lea rdx,[rbp+0x30]; mov r12,[rbp-0x70]; mov rcx,r12` | 15 | - |
//! | enemy bar | `cmp [rsp+0x31],r12b; jne +0x375; cmp [rsp+0x32],r12b` | 16 | `jne rel32` |
//!
//! ilhook 2.3 relocates a `jcc rel32` as an inverted short `jcc` over `jmp [rip+table]`
//! (`move_inst.rs`, `FlowControl::ConditionalBranch`), so the moved test still reaches the
//! original target. No window holds a `call`, a RIP-relative operand or a branch target: every
//! direct `jmp`, `jcc` and `call` target of both functions was collected and none lands inside a
//! window, and neither function has an indirect jump. Neither function keeps `XMM4`/`XMM5` live
//! across a site (ilhook saves only `XMM0`-`XMM3`): the producer first writes `XMM4` at `+0x648`,
//! after the call at `+0x616`, and `UpdateUI` never names either.

#![cfg_attr(not(windows), allow(dead_code))]

use core::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

use super::{MidHook, Window};
use crate::addr::tags;

include!(concat!(env!("OUT_DIR"), "/generated_tag_windows.rs"));

/// The live companions' `FieldInsHandle`s, at most one per tag row.
static HANDLES: [AtomicU64; tags::SLOTS as usize] = [const { AtomicU64::new(0) }; 7];
/// How many of [`HANDLES`] are current. Zero keeps every routine on the original path without a
/// single read of game memory.
static COUNT: AtomicUsize = AtomicUsize::new(0);

static TAG_SLOT_RESUME: AtomicUsize = AtomicUsize::new(0);
static TAG_SLOT_ACTED: AtomicU64 = AtomicU64::new(0);
static TAG_COLOUR_RESUME: AtomicUsize = AtomicUsize::new(0);
static TAG_COLOUR_ACTED: AtomicU64 = AtomicU64::new(0);
static ENEMY_BAR_RESUME: AtomicUsize = AtomicUsize::new(0);
static ENEMY_BAR_ACTED: AtomicU64 = AtomicU64::new(0);

/// Replace the companion set. Called from the game task with the handles of the companions that
/// are alive; anything past the seventh is dropped, since the list has seven rows.
pub(crate) fn publish(handles: &[u64]) {
    let n = handles.len().min(HANDLES.len());
    // Shrink the count before the slots change and grow it after, so a reader on another thread
    // sees either the old or the new set of slots, never an unwritten one.
    if n < COUNT.load(Ordering::Acquire) {
        COUNT.store(n, Ordering::Release);
    }
    for (k, handle) in handles.iter().take(n).enumerate() {
        HANDLES[k].store(*handle, Ordering::Release);
    }
    COUNT.store(n, Ordering::Release);
}

/// The published handles, for the log.
pub(crate) fn published() -> Vec<u64> {
    let n = COUNT.load(Ordering::Acquire);
    HANDLES[..n.min(HANDLES.len())]
        .iter()
        .map(|slot| slot.load(Ordering::Acquire))
        .collect()
}

fn is_companion(handle: u64) -> bool {
    let n = COUNT.load(Ordering::Acquire).min(HANDLES.len());
    handle != 0
        && handle != u64::MAX
        && HANDLES[..n]
            .iter()
            .any(|slot| slot.load(Ordering::Acquire) == handle)
}

/// The handle that tag row `index` should show when the party fills `members` rows, if the row
/// is one of the companions'.
fn companion_for_slot(index: i32, members: i32) -> Option<u64> {
    if !(0..tags::SLOTS).contains(&index) || index < members {
        return None;
    }
    let k = usize::try_from(index.checked_sub(members.max(0))?).ok()?;
    let n = COUNT.load(Ordering::Acquire).min(HANDLES.len());
    (k < n).then(|| HANDLES[k].load(Ordering::Acquire))
}

/// `+0x2f8`: a row past the party's members -> companion's handle in `[rbp-0x78]`, resume after
/// the handle call.
///
/// # Safety
///
/// Installed by ilhook as the jmp-ret routine of [`TAG_SLOT`]; never called from Rust outside
/// tests. `RSP` and `RBP` are the producer's frame, so `[rsp+0x50]` is its member count and
/// `[rbp-0x78]` the handle local the member path passes to `GetFieldInsHandleByMemberIndex`.
unsafe extern "win64" fn tag_slot_route(
    regs: *mut ilhook::x64::Registers,
    original: usize,
    _user_data: usize,
) -> usize {
    let resume = TAG_SLOT_RESUME.load(Ordering::Acquire);
    if resume == 0 || COUNT.load(Ordering::Acquire) == 0 {
        return original;
    }
    let regs = unsafe { &mut *regs };
    let index = regs.r15 as u32 as i32;
    // SAFETY: the producer's own stack local, written at `+0x92` before the loop.
    let members =
        unsafe { core::ptr::read_volatile((regs.rsp + tags::MEMBER_COUNT_LOCAL) as *const i32) };
    let Some(handle) = companion_for_slot(index, members) else {
        return original;
    };
    let local = regs.rbp - tags::SLOT_HANDLE_LOCAL;
    // SAFETY: the producer's own 8-byte handle local, the one the skipped call would write.
    unsafe { core::ptr::write_volatile(local as *mut u64, handle) };
    regs.rax = local;
    TAG_SLOT_ACTED.fetch_add(1, Ordering::Relaxed);
    resume
}

/// `+0x607`: a companion's row -> `AL` = friendly, so the moved store records it.
///
/// # Safety
///
/// Installed by ilhook as the jmp-ret routine of [`TAG_COLOUR`]. `R15` is the row's `ChrIns*`
/// here (`mov r15,rax` at `+0x35f`, null rejected at `+0x365`), and the game passed it to
/// `GetRoleNameColor` one instruction earlier, so it is live.
unsafe extern "win64" fn tag_colour_route(
    regs: *mut ilhook::x64::Registers,
    original: usize,
    _user_data: usize,
) -> usize {
    if COUNT.load(Ordering::Acquire) == 0 {
        return original;
    }
    let regs = unsafe { &mut *regs };
    if regs.r15 == 0 {
        return original;
    }
    // SAFETY: `ChrIns+0x8`, the `FieldInsHandle` of a live character (see above).
    let handle = unsafe {
        core::ptr::read_volatile((regs.r15 + crate::addr::chr_ins::HANDLE as u64) as *const u64)
    };
    if is_companion(handle) {
        regs.rax = (regs.rax & !0xff) | u64::from(tags::FRIENDLY_NAME_COLOUR);
        TAG_COLOUR_ACTED.fetch_add(1, Ordering::Relaxed);
    }
    original
}

/// `+0x26e`: a companion that was hit -> the shared tail at `+0x375`, past the enemy-bar call.
///
/// # Safety
///
/// Installed by ilhook as the jmp-ret routine of [`ENEMY_BAR`]. `RDI` is the `CSChrDamageModule`
/// for the whole function, and `[rdi+8]` is the character that owns it.
unsafe extern "win64" fn enemy_bar_route(
    regs: *mut ilhook::x64::Registers,
    original: usize,
    _user_data: usize,
) -> usize {
    let resume = ENEMY_BAR_RESUME.load(Ordering::Acquire);
    if resume == 0 || COUNT.load(Ordering::Acquire) == 0 {
        return original;
    }
    let regs = unsafe { &*regs };
    // SAFETY: the module's owner pointer, which `UpdateUI` has already read on this call.
    let owner =
        unsafe { core::ptr::read_volatile((regs.rdi + tags::DAMAGE_MODULE_OWNER) as *const u64) };
    if owner == 0 {
        return original;
    }
    // SAFETY: `ChrIns+0x8` of the module's live owner.
    let handle = unsafe {
        core::ptr::read_volatile((owner + crate::addr::chr_ins::HANDLE as u64) as *const u64)
    };
    if !is_companion(handle) {
        return original;
    }
    ENEMY_BAR_ACTED.fetch_add(1, Ordering::Relaxed);
    resume
}

pub(crate) const TAG_SLOT: MidHook = MidHook {
    name: "friendly tag slot",
    function_rva: tags::FRIENDLY_TAG_PRODUCER,
    hook_offset: tags::SLOT_TEST,
    expected_window: Window {
        bytes: TAG_SLOT_TEST_WINDOW,
        mask: TAG_SLOT_TEST_WINDOW_MASK,
    },
    resume: Some((
        tags::SLOT_HANDLE_READY,
        Window {
            bytes: TAG_SLOT_HANDLE_READY_WINDOW,
            mask: TAG_SLOT_HANDLE_READY_WINDOW_MASK,
        },
    )),
    routine: tag_slot_route,
    resume_address: &TAG_SLOT_RESUME,
    acted: &TAG_SLOT_ACTED,
    rationale: "tag rows past the party's members show the companions: their handle goes in \
                [rbp-0x78] and the producer resumes after the handle call (+0x31c)",
};

pub(crate) const TAG_COLOUR: MidHook = MidHook {
    name: "friendly tag colour",
    function_rva: tags::FRIENDLY_TAG_PRODUCER,
    hook_offset: tags::COLOUR_STORE,
    expected_window: Window {
        bytes: TAG_COLOUR_STORE_WINDOW,
        mask: TAG_COLOUR_STORE_WINDOW_MASK,
    },
    resume: None,
    routine: tag_colour_route,
    resume_address: &TAG_COLOUR_RESUME,
    acted: &TAG_COLOUR_ACTED,
    rationale: "a companion's row stores roleNameColor 1 (friendly) instead of its NpcParam \
                row's 2",
};

pub(crate) const ENEMY_BAR: MidHook = MidHook {
    name: "enemy damage bar",
    function_rva: tags::DAMAGE_UPDATE_UI,
    hook_offset: tags::ENEMY_BAR_TEST,
    expected_window: Window {
        bytes: ENEMY_BAR_TEST_WINDOW,
        mask: ENEMY_BAR_TEST_WINDOW_MASK,
    },
    resume: Some((
        tags::ENEMY_BAR_TAIL,
        Window {
            bytes: ENEMY_BAR_TAIL_WINDOW,
            mask: ENEMY_BAR_TAIL_WINDOW_MASK,
        },
    )),
    routine: enemy_bar_route,
    resume_address: &ENEMY_BAR_RESUME,
    acted: &ENEMY_BAR_ACTED,
    rationale: "a hit companion skips SetEntityHealthBarDamage and takes the shared tail \
                (+0x375), so no red enemy bar is listed for it",
};

#[cfg(windows)]
pub(crate) use game::{install_voice_chat_guard, tick};

#[cfg(windows)]
mod game {
    use core::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Mutex;

    use er_game_base::mem::{safe_read_i32, safe_read_usize};

    use super::{is_companion, publish, published};
    use crate::addr::{self, lab, tags};
    use crate::log::summons_log;

    static ORIG_VOICE: AtomicUsize = AtomicUsize::new(0);
    static VOICE_NULLED: AtomicUsize = AtomicUsize::new(0);
    /// The set last logged, so the log carries changes rather than a line a frame.
    static LOGGED: Mutex<Vec<u64>> = Mutex::new(Vec::new());

    type VoiceChatFn = unsafe extern "system" fn(usize, usize, usize) -> usize;

    unsafe extern "system" fn voice_chat_detour(this: usize, out: usize, player: usize) -> usize {
        let original = ORIG_VOICE.load(Ordering::Acquire);
        // SAFETY: the trampoline of the function this replaces.
        let original: VoiceChatFn = unsafe { core::mem::transmute(original) };
        if player != 0 {
            // SAFETY: `PlayerIns+0x8`, the handle of the live character the caller passed.
            let handle =
                unsafe { core::ptr::read_volatile((player + addr::chr_ins::HANDLE) as *const u64) };
            if is_companion(handle) {
                if VOICE_NULLED.fetch_add(1, Ordering::Relaxed) == 0 {
                    summons_log(format_args!(
                        "tags: first voice-chat lookup for companion 0x{player:x} answered as no \
                         player"
                    ));
                }
                // SAFETY: the function null-checks `R8` before its first use and returns "none".
                return unsafe { original(this, out, 0) };
            }
        }
        // SAFETY: forwarded unchanged.
        unsafe { original(this, out, player) }
    }

    /// Detour the voice-chat lookup. Called once, from the install thread, before the mid hooks
    /// are armed: a companion must never reach it with its own `PlayerIns`.
    pub(crate) fn install_voice_chat_guard() -> bool {
        // SAFETY: MinHook init is idempotent across DLLs.
        match unsafe { er_hook::MH_Initialize() } {
            er_hook::MH_STATUS::MH_OK | er_hook::MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
            status => {
                summons_log(format_args!("tags: MH_Initialize failed: {status:?}"));
                return false;
            }
        }
        match crate::mimic_hooks::hook(
            tags::VOICE_CHAT_LOOKUP,
            voice_chat_detour as *const () as usize,
            &ORIG_VOICE,
            "VoiceChatLookup",
        ) {
            // SAFETY: applies the queued enable.
            Ok(_hook) => match unsafe { er_hook::MH_ApplyQueued() } {
                er_hook::MH_STATUS::MH_OK => {
                    summons_log(format_args!(
                        "tags: voice-chat lookup detoured; a companion is looked up as no player"
                    ));
                    true
                }
                status => {
                    summons_log(format_args!("tags: MH_ApplyQueued failed: {status:?}"));
                    false
                }
            },
            Err(why) => {
                summons_log(format_args!(
                    "tags: voice-chat guard off, so companions stay out of the tag list -- {why}"
                ));
                false
            }
        }
    }

    /// HP of `chr` (`ChrIns+0x190` -> data module `+0x138`), or `None` when it does not read.
    fn hp(chr: usize) -> Option<i32> {
        // SAFETY: fault-tolerant reads.
        let modules = unsafe { safe_read_usize(chr + addr::physics::CHR_MODULES) }?;
        let data = unsafe { safe_read_usize(modules + lab::MODULE_DATA) }.filter(|&d| d != 0)?;
        unsafe { safe_read_i32(data + lab::DATA_HP) }
    }

    /// Publish the living companions for the tag hooks, and log the set when it changes. A
    /// companion at 0 HP is left out: it still resolves from its handle (measured in the Frida
    /// prototype), and its row would show an empty bar over a body.
    pub(crate) fn tick() {
        let handles: Vec<u64> = crate::team::companions()
            .into_iter()
            .filter(|&chr| hp(chr).is_some_and(|hp| hp > 0))
            .filter_map(crate::game::chr_handle)
            .collect();
        publish(&handles);
        let now = published();
        if let Ok(mut logged) = LOGGED.try_lock()
            && *logged != now
        {
            summons_log(format_args!(
                "tags: {} companion(s) in the friendly tag list: {:x?}",
                now.len(),
                now
            ));
            *logged = now;
        }
        super::super::report();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The routines share one companion table, so the tests that publish run under one lock.
    static TABLE: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn regs() -> ilhook::x64::Registers {
        // SAFETY: `Registers` is plain integers.
        unsafe { core::mem::zeroed() }
    }

    #[test]
    fn offsets_match_the_static_re() {
        assert_eq!(tags::FRIENDLY_TAG_PRODUCER, 0x77_7380);
        assert_eq!(
            (tags::SLOT_TEST, tags::SLOT_HANDLE_READY, tags::COLOUR_STORE),
            (0x2f8, 0x31c, 0x607)
        );
        assert_eq!(tags::DAMAGE_UPDATE_UI, 0x44_9280);
        assert_eq!((tags::ENEMY_BAR_TEST, tags::ENEMY_BAR_TAIL), (0x26e, 0x375));
        assert_eq!(tags::VOICE_CHAT_LOOKUP, 0x00cb_a510);
    }

    #[test]
    fn only_the_resume_pin_has_a_masked_displacement() {
        assert!(TAG_SLOT_TEST_WINDOW_MASK.iter().all(|&m| m == 0xff));
        assert!(TAG_COLOUR_STORE_WINDOW_MASK.iter().all(|&m| m == 0xff));
        assert!(ENEMY_BAR_TEST_WINDOW_MASK.iter().all(|&m| m == 0xff));
        assert_eq!(
            TAG_SLOT_HANDLE_READY_WINDOW_MASK,
            [0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00]
        );
        // The 1.17.1 bytes at the same offset: only the displacement differs.
        let live = [0x48, 0x8b, 0x0d, 0xd5, 0x1a, 0x5f, 0x03];
        let window = Window {
            bytes: TAG_SLOT_HANDLE_READY_WINDOW,
            mask: TAG_SLOT_HANDLE_READY_WINDOW_MASK,
        };
        assert!(super::super::window_matches(&live, &window));
    }

    #[test]
    fn rows_past_the_members_get_the_companions_in_order() {
        let _table = TABLE.lock().unwrap_or_else(|e| e.into_inner());
        publish(&[0xaa, 0xbb]);
        assert_eq!(companion_for_slot(0, 1), None, "a member row is the game's");
        assert_eq!(companion_for_slot(1, 1), Some(0xaa));
        assert_eq!(companion_for_slot(2, 1), Some(0xbb));
        assert_eq!(companion_for_slot(3, 1), None, "only two companions");
        assert_eq!(companion_for_slot(-1, 0), None);
        assert_eq!(companion_for_slot(7, 6), None, "there are seven rows");
        publish(&[0x1, 0x2, 0x3]);
        assert_eq!(
            companion_for_slot(6, 6),
            Some(0x1),
            "a full party leaves one row"
        );
        publish(&[]);
        assert_eq!(companion_for_slot(1, 1), None);
    }

    #[test]
    fn more_than_seven_companions_are_cut_to_seven() {
        let _table = TABLE.lock().unwrap_or_else(|e| e.into_inner());
        publish(&[1, 2, 3, 4, 5, 6, 7, 8, 9]);
        assert_eq!(published(), vec![1, 2, 3, 4, 5, 6, 7]);
        publish(&[]);
    }

    #[test]
    fn the_slot_route_writes_the_handle_and_resumes_after_the_call() {
        let _table = TABLE.lock().unwrap_or_else(|e| e.into_inner());
        let mut stack = [0_u64; 0x40];
        let rsp = stack.as_mut_ptr() as u64;
        let rbp = rsp + 0x100;
        // [rsp+0x50] = one member.
        stack[0x50 / 8] = 1;
        stack[(0x100 - 0x78) / 8] = 0xffff_ffff_ffff_ffff;
        let mut regs = regs();
        regs.rsp = rsp;
        regs.rbp = rbp;
        regs.r15 = 0xdead_0000_0000_0001; // r15d = 1; the high half is not the index
        publish(&[0x1234_5678_9abc]);
        TAG_SLOT_RESUME.store(0x5000, Ordering::SeqCst);
        let back = unsafe { tag_slot_route(&mut regs, 0x1111, 0) };
        assert_eq!(back, 0x5000);
        assert_eq!(stack[(0x100 - 0x78) / 8], 0x1234_5678_9abc);
        assert_eq!(regs.rax, rbp - 0x78, "RAX as the skipped leaf returns it");

        regs.r15 = 0; // a member row
        let back = unsafe { tag_slot_route(&mut regs, 0x1111, 0) };
        assert_eq!(back, 0x1111, "a member row runs the moved test");
        regs.r15 = 2; // past the one companion
        assert_eq!(unsafe { tag_slot_route(&mut regs, 0x1111, 0) }, 0x1111);

        TAG_SLOT_RESUME.store(0, Ordering::SeqCst);
        regs.r15 = 1;
        assert_eq!(
            unsafe { tag_slot_route(&mut regs, 0x1111, 0) },
            0x1111,
            "an unresolved resume address is never jumped to"
        );
        publish(&[]);
    }

    #[test]
    fn the_colour_route_sets_al_only_for_a_companion() {
        let _table = TABLE.lock().unwrap_or_else(|e| e.into_inner());
        let chr = [0_u64, 0x77, 0];
        let mut regs = regs();
        regs.r15 = chr.as_ptr() as u64;
        regs.rax = 0x1234_5602;
        publish(&[0x77]);
        assert_eq!(unsafe { tag_colour_route(&mut regs, 0x2222, 0) }, 0x2222);
        assert_eq!(regs.rax, 0x1234_5601, "only AL changes");

        let stranger = [0_u64, 0x99, 0];
        regs.r15 = stranger.as_ptr() as u64;
        regs.rax = 0x02;
        assert_eq!(unsafe { tag_colour_route(&mut regs, 0x2222, 0) }, 0x2222);
        assert_eq!(regs.rax, 0x02);
        publish(&[]);
    }

    #[test]
    fn the_enemy_bar_route_skips_only_a_companion() {
        let _table = TABLE.lock().unwrap_or_else(|e| e.into_inner());
        let owner = [0_u64, 0x55];
        let module = [0_u64, owner.as_ptr() as u64];
        let mut regs = regs();
        regs.rdi = module.as_ptr() as u64;
        publish(&[0x55]);
        ENEMY_BAR_RESUME.store(0x6000, Ordering::SeqCst);
        assert_eq!(unsafe { enemy_bar_route(&mut regs, 0x3333, 0) }, 0x6000);

        let other = [0_u64, 0x66];
        let module = [0_u64, other.as_ptr() as u64];
        regs.rdi = module.as_ptr() as u64;
        assert_eq!(unsafe { enemy_bar_route(&mut regs, 0x3333, 0) }, 0x3333);

        let module = [0_u64, 0];
        regs.rdi = module.as_ptr() as u64;
        assert_eq!(unsafe { enemy_bar_route(&mut regs, 0x3333, 0) }, 0x3333);
        ENEMY_BAR_RESUME.store(0, Ordering::SeqCst);
        publish(&[]);
    }

    #[test]
    fn no_companions_means_no_reads() {
        let _table = TABLE.lock().unwrap_or_else(|e| e.into_inner());
        publish(&[]);
        let mut regs = regs();
        // Null frame and module: a routine that read anything would fault here.
        TAG_SLOT_RESUME.store(0x5000, Ordering::SeqCst);
        ENEMY_BAR_RESUME.store(0x6000, Ordering::SeqCst);
        regs.r15 = 1;
        assert_eq!(unsafe { tag_slot_route(&mut regs, 0x10, 0) }, 0x10);
        assert_eq!(unsafe { tag_colour_route(&mut regs, 0x20, 0) }, 0x20);
        assert_eq!(unsafe { enemy_bar_route(&mut regs, 0x30, 0) }, 0x30);
        TAG_SLOT_RESUME.store(0, Ordering::SeqCst);
        ENEMY_BAR_RESUME.store(0, Ordering::SeqCst);
    }
}
