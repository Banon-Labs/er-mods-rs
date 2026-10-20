//! `CS::MapItemManImpl::GiveItems`, guarded at the two loads of a local player that may be absent.
//!
//! # The crash
//!
//! A tester's game died on 2026-10-19 with one access violation at `eldenring.exe+0x5615e0`
//! (1.17.1), the `MOV RAX,[RCX]` right after `GiveItems` loads `WorldChrMan->mainPlayerIns`:
//!
//! ```text
//! +0x1d9: MOV RCX, qword ptr [RCX + 0x1e508]   ; WorldChrMan->mainPlayerIns
//! +0x1e0: MOV RAX, qword ptr [RCX]             ; its vtable -- RCX is null
//! +0x1e3: CALL qword ptr [RAX + 0x168]         ; ChrIns::GetPlayerGameData
//! ```
//!
//! The same three instructions appear again at `+0x34a`. Seamless reaches both through
//! `MapItemManImpl::PacketReceive` case 6, a client receiving the host's pickup approval while it
//! has no `PlayerIns` (during a load, for one). Offsets are the same in 1.16.2 and 1.17.1: the
//! function is `IDENTICAL-WHOLE` between them.
//!
//! # Why two mid hooks and not one entry guard
//!
//! The entry guard this replaces returned before giving anything, so the item in that packet was
//! lost even when the inventory had room and neither load would have run. These hooks let the
//! function give what fits and only skip the two branches that need the player.
//!
//! # Where each one goes when there is no player
//!
//! * `+0x1d9` is the storage-chest overflow. The function already has a branch for "no storage
//!   chest": the `JE` at `+0x1f6` jumps to `+0x2f3`, which reads the overflow amount from
//!   `[RSP+0x34]` and rejoins the loop. A missing player has no storage chest either, so the hook
//!   resumes there. Nothing it reads is set by the skipped code: `[RSP+0x34]` is the zeroed local
//!   the same branch reads, `R15` is reloaded from `[RSP+0x70]` at `+0x2f7`, and `RBX` (the amount
//!   left over) is untouched.
//! * `+0x34a` classifies the pickup for the server log by asking whether this player gave the item
//!   to themselves. With no player that cannot be true, so the hook resumes at `+0x3c1`, the
//!   "someone else's item" branch. That branch reads `ESI`, the giver's player id, which the
//!   skipped code would have loaded from `[R15+4]` at `+0x39e`; the routine loads it the same way.
//!   `EDI` is masked by the branch itself (`AND EDI,-2`), which is what the skipped `OR EDI,1` /
//!   `AND EDI,-2` pair also left, and `RBX` is written by the branch before it is read.
//!
//! No branch in `GiveItems` lands inside either 16-byte window; checked against both images'
//! disassembly on 2026-10-19 by collecting every jump and call target in the function.

use core::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

use super::MidHook;

/// `void CS::MapItemManImpl::GiveItems(MapItemManImpl *, ItemLotSpawnDataList *,
/// ItemLotSpawnDataStatus *, DL_BOOL)`. 1.17.1 `0x140561400`.
pub(crate) const GIVE_ITEMS_RVA: usize = 0x56_05b0;
pub(crate) const STORAGE_OVERFLOW_LOAD: usize = 0x1d9;
pub(crate) const STORAGE_ABSENT_JOIN: usize = 0x2f3;
pub(crate) const PICKUP_LOG_LOAD: usize = 0x34a;
pub(crate) const PICKUP_NOT_MINE: usize = 0x3c1;

pub(crate) use er_game_base::rva::WORLD_CHR_MAN_PLAYER_INS_OFFSET;

/// `ItemLotSpawnDataStatus+4`, the giver's player id, read at `+0x39e` as `MOV ESI,[R15+4]`.
pub(crate) const STATUS_FROM_PLAYER_ID_OFFSET: u64 = 4;

pub(crate) static STORAGE_RESUME: AtomicUsize = AtomicUsize::new(0);
pub(crate) static PICKUP_LOG_RESUME: AtomicUsize = AtomicUsize::new(0);
pub(crate) static STORAGE_DIVERTED: AtomicU64 = AtomicU64::new(0);
pub(crate) static PICKUP_LOG_DIVERTED: AtomicU64 = AtomicU64::new(0);

include!(concat!(env!("OUT_DIR"), "/generated_give_items_windows.rs"));

/// Whether the `WorldChrMan` in `world_chr_man` has no local player.
///
/// # Safety
///
/// `world_chr_man` must be the `WorldChrManImp` pointer `GiveItems` holds in `RCX` at both hook
/// sites. The code before each site loads it from the singleton slot and `DLPanic`s if the slot is
/// null, so it is a live object whenever a hook runs.
unsafe fn main_player_missing(world_chr_man: u64) -> bool {
    let slot = (world_chr_man + WORLD_CHR_MAN_PLAYER_INS_OFFSET as u64) as *const u64;
    unsafe { core::ptr::read_volatile(slot) == 0 }
}

/// `+0x1d9`: no player -> the no-storage-chest join at `+0x2f3`.
///
/// # Safety
///
/// Installed by ilhook as the jmp-ret routine of [`STORAGE_OVERFLOW`]; never called from Rust.
unsafe extern "win64" fn storage_overflow_route(
    regs: *mut ilhook::x64::Registers,
    original: usize,
    _user_data: usize,
) -> usize {
    let resume = STORAGE_RESUME.load(Ordering::Acquire);
    let regs = unsafe { &*regs };
    if resume == 0 || !unsafe { main_player_missing(regs.rcx) } {
        return original;
    }
    STORAGE_DIVERTED.fetch_add(1, Ordering::Relaxed);
    resume
}

/// `+0x34a`: no player -> the "someone else's item" branch at `+0x3c1`, with `ESI` loaded as the
/// skipped `MOV ESI,[R15+4]` would have.
///
/// # Safety
///
/// Installed by ilhook as the jmp-ret routine of [`PICKUP_LOG`]; never called from Rust. `R15`
/// holds the `ItemLotSpawnDataStatus` at this site (reloaded from `[RSP+0x70]` at `+0x2f7` and read
/// as `[R15]` at `+0x305`).
unsafe extern "win64" fn pickup_log_route(
    regs: *mut ilhook::x64::Registers,
    original: usize,
    _user_data: usize,
) -> usize {
    let resume = PICKUP_LOG_RESUME.load(Ordering::Acquire);
    let regs = unsafe { &mut *regs };
    if resume == 0 || !unsafe { main_player_missing(regs.rcx) } {
        return original;
    }
    let from_player = (regs.r15 + STATUS_FROM_PLAYER_ID_OFFSET) as *const u32;
    regs.rsi = u64::from(unsafe { core::ptr::read_volatile(from_player) });
    PICKUP_LOG_DIVERTED.fetch_add(1, Ordering::Relaxed);
    resume
}

pub(crate) const STORAGE_OVERFLOW: MidHook = MidHook {
    name: "GiveItems storage overflow",
    function_rva: GIVE_ITEMS_RVA,
    hook_offset: STORAGE_OVERFLOW_LOAD,
    expected_window: STORAGE_OVERFLOW_LOAD_WINDOW,
    resume_offset: STORAGE_ABSENT_JOIN,
    expected_resume: STORAGE_ABSENT_JOIN_WINDOW,
    routine: storage_overflow_route,
    resume: &STORAGE_RESUME,
    diverted: &STORAGE_DIVERTED,
    rationale: "no WorldChrMan->mainPlayerIns -> take the function's own no-storage-chest branch \
                (+0x2f3) instead of dereferencing the null player; what fit in the inventory is \
                still given",
};

pub(crate) const PICKUP_LOG: MidHook = MidHook {
    name: "GiveItems pickup log",
    function_rva: GIVE_ITEMS_RVA,
    hook_offset: PICKUP_LOG_LOAD,
    expected_window: PICKUP_LOG_LOAD_WINDOW,
    resume_offset: PICKUP_NOT_MINE,
    expected_resume: PICKUP_NOT_MINE_WINDOW,
    routine: pickup_log_route,
    resume: &PICKUP_LOG_RESUME,
    diverted: &PICKUP_LOG_DIVERTED,
    rationale: "no WorldChrMan->mainPlayerIns -> log the pickup as another player's item (+0x3c1, \
                ESI from [R15+4]) instead of dereferencing the null player",
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offsets_match_er_1162_static_re() {
        assert_eq!(GIVE_ITEMS_RVA, 0x56_05b0);
        assert_eq!(
            (
                STORAGE_OVERFLOW_LOAD,
                STORAGE_ABSENT_JOIN,
                PICKUP_LOG_LOAD,
                PICKUP_NOT_MINE
            ),
            (0x1d9, 0x2f3, 0x34a, 0x3c1)
        );
        assert_eq!(WORLD_CHR_MAN_PLAYER_INS_OFFSET, 0x1_e508);
    }

    /// ilhook overwrites 14 bytes, so the checked window must cover at least that much, and it must
    /// end on an instruction boundary: the window is the three moved instructions, 16 bytes.
    #[test]
    fn windows_cover_the_jump() {
        const ILHOOK_JUMP_BYTES: usize = 14;
        for window in [STORAGE_OVERFLOW_LOAD_WINDOW, PICKUP_LOG_LOAD_WINDOW] {
            assert!(window.len() >= ILHOOK_JUMP_BYTES);
            assert_eq!(window.len(), 16);
        }
    }

    #[test]
    fn routines_take_the_original_path_until_resolved() {
        STORAGE_RESUME.store(0, Ordering::SeqCst);
        let mut regs: ilhook::x64::Registers = unsafe { core::mem::zeroed() };
        let back = unsafe { storage_overflow_route(&mut regs, 0x1234, 0) };
        assert_eq!(
            back, 0x1234,
            "an unresolved resume address must never be jumped to"
        );
    }

    #[test]
    fn the_pickup_log_route_loads_the_giver_into_esi() {
        let world: [u64; 0x1e510 / 8] = [0; 0x1e510 / 8];
        let status: [u32; 2] = [7, 0xbeef];
        let mut regs: ilhook::x64::Registers = unsafe { core::mem::zeroed() };
        regs.rcx = world.as_ptr() as u64;
        regs.r15 = status.as_ptr() as u64;
        regs.rsi = 0xffff_ffff_ffff_ffff;
        PICKUP_LOG_RESUME.store(0x5000, Ordering::SeqCst);
        let back = unsafe { pickup_log_route(&mut regs, 0x1234, 0) };
        PICKUP_LOG_RESUME.store(0, Ordering::SeqCst);
        assert_eq!(back, 0x5000);
        assert_eq!(
            regs.rsi, 0xbeef,
            "ESI must hold the giver, zero-extended like a 32-bit MOV"
        );
    }
}
