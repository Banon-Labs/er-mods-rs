//! `CS::MapItemManImpl::GiveItems`, guarded against a session with no local `PlayerIns`.
//!
//! # The crash
//!
//! A tester's game died on 2026-10-19 16:18Z with one access violation at `eldenring.exe+0x5615e0`
//! (1.17.1), followed by the 23 `0xc0000026` records an unwind that cannot cross a detoured frame
//! leaves behind. That address is the `MOV RAX,[RCX]` in
//!
//! ```text
//! 1405615d9: MOV RCX, qword ptr [RCX + 0x1e508]   ; WorldChrMan->mainPlayerIns
//! 1405615e0: MOV RAX, qword ptr [RCX]             ; its vtable -- RCX is null
//! 1405615e3: CALL qword ptr [RAX + 0x168]         ; ChrIns::GetPlayerGameData
//! ```
//!
//! which is the branch that moves whatever did not fit in the inventory into the storage chest.
//! The same two loads appear again at `0x14056174a`, where the function classifies the pickup for
//! the server log by comparing the giver against the local character. Both read the local player
//! without testing it, because in vanilla a give only ever arrives while that player exists.
//!
//! # Why Seamless reaches it
//!
//! `CS::MapItemManImpl::PacketReceive` calls `GiveItems` from its case 6: a client receiving the
//! host's approval for an item lot it picked up. Seamless delivers that packet whenever it
//! arrives, including while the client has no `PlayerIns` -- during a load, for one. The other
//! three callers (`_RequestRegistGetItem` and two local pickup paths) start from the local player
//! and cannot get here without one.
//!
//! # Why the early return is the right answer
//!
//! `GiveItems` returns nothing, and it already has a path that gives nothing: an item list whose
//! count is 0 or above 10 jumps straight from `0x140561454` to the epilogue at `0x140561943`. The
//! stub returns the same way. Nothing has been written at that point -- no acquisition flag, no
//! award queue entry, no inventory change -- so the game is left in the state it would be in had
//! the packet never arrived.
//!
//! The cost is the item in that packet, and only when the player is absent. A guard placed on the
//! two faulting loads instead would keep the item in the common case, but both sit in the middle
//! of the function, and a mid-function detour is the thing `resolve_detour_address` refuses (see
//! `er_game_base::game_build`). A one-byte patch cannot add a null test.
//!
//! # Coverage
//!
//! A group of one.

use core::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

use super::Guard;

/// `void CS::MapItemManImpl::GiveItems(MapItemManImpl *, ItemLotSpawnDataList *,
/// ItemLotSpawnDataStatus *, DL_BOOL)`. 1.17.1 `0x140561400`.
pub(crate) const GIVE_ITEMS_RVA: usize = 0x56_05b0;

pub(crate) use er_game_base::rva::{WORLD_CHR_MAN_GLOBAL_RVA, WORLD_CHR_MAN_PLAYER_INS_OFFSET};

/// Absolute address of the `WorldChrMan` singleton slot, published by [`publish_world_chr_man_slot`]
/// before the hook is enabled. Zero means unresolved, and the stub then passes every call through.
pub(crate) static WORLD_CHR_MAN_SLOT: AtomicUsize = AtomicUsize::new(0);

pub(crate) static ORIG_GIVE_ITEMS: AtomicUsize = AtomicUsize::new(super::ORIGINAL_UNSET);
pub(crate) static BLOCKED_GIVE_ITEMS: AtomicU64 = AtomicU64::new(0);

// The expected prologue, assembled from named `iced-x86` instructions by this crate's `build.rs`
// and verified there against `eldenring-deobf.bin` when a copy is present.
include!(concat!(
    env!("OUT_DIR"),
    "/generated_null_main_player_prologues.rs"
));

/// Resolve the singleton slot for this process. Called before the hook is enabled.
pub(crate) fn publish_world_chr_man_slot(base: usize) {
    WORLD_CHR_MAN_SLOT.store(
        er_game_base::mem::game_data_addr(
            base,
            WORLD_CHR_MAN_GLOBAL_RVA,
            "WORLD_CHR_MAN_GLOBAL_RVA",
        ),
        Ordering::SeqCst,
    );
}

/// No `WorldChrMan`, or no local `PlayerIns` in it -> return as the empty-list path does.
///
/// # Safety
///
/// Installed as a MinHook detour on [`GIVE_ITEMS_RVA`]; never called from Rust. Entering it before
/// `ORIG_GIVE_ITEMS` holds a trampoline would jump to zero, which is why installation publishes
/// that slot before enabling the hook.
#[cfg(windows)]
#[unsafe(naked)]
pub(crate) unsafe extern "system" fn give_items_guard() {
    core::arch::naked_asm!(
        // `r11` is volatile and carries no argument, so the pass-through path reaches the original
        // with `rcx`, `rdx`, `r8`, `r9` and the stack exactly as they arrived.
        "mov r11, qword ptr [rip + {slot}]",
        "test r11, r11",
        // Unresolved slot: pass through rather than guard on a guess.
        "jz 2f",
        "mov r11, qword ptr [r11]",
        "test r11, r11",
        "jz 1f",
        "mov r11, qword ptr [r11 + {player}]",
        "test r11, r11",
        "jnz 2f",
        "1:",
        "lock inc qword ptr [rip + {blocked}]",
        "ret",
        "2:",
        "jmp qword ptr [rip + {original}]",
        slot = sym WORLD_CHR_MAN_SLOT,
        player = const WORLD_CHR_MAN_PLAYER_INS_OFFSET,
        blocked = sym BLOCKED_GIVE_ITEMS,
        original = sym ORIG_GIVE_ITEMS,
    )
}

// The host build has no game to hook; the stub exists only so the registry still names a function
// pointer and the crate stays checkable off-target.
#[cfg(not(windows))]
pub(crate) unsafe extern "system" fn give_items_guard() {}

pub(crate) const GIVE_ITEMS_GUARD: Guard = Guard {
    name: "MapItemMan GiveItems",
    group: "null_main_player",
    rva: GIVE_ITEMS_RVA,
    expected_prologue: GIVE_ITEMS_PROLOGUE,
    detour: give_items_guard,
    original: &ORIG_GIVE_ITEMS,
    blocked: &BLOCKED_GIVE_ITEMS,
    prepare: Some(publish_world_chr_man_slot),
    rationale: "no WorldChrMan->mainPlayerIns -> return as the empty item list does (count 0, \
                straight to the epilogue) instead of dereferencing the null player in the storage \
                overflow and pickup-log branches; costs the item in a host approval packet that \
                lands while the client has no PlayerIns",
};

#[cfg(test)]
mod tests {
    use super::*;

    /// The hook address and the two values the stub tests are the whole safety argument.
    #[test]
    fn addresses_match_er_1162_static_re() {
        assert_eq!(GIVE_ITEMS_RVA, 0x56_05b0);
        assert_eq!(WORLD_CHR_MAN_GLOBAL_RVA, 0x3d6_5f88);
        assert_eq!(WORLD_CHR_MAN_PLAYER_INS_OFFSET, 0x1_e508);
    }

    #[test]
    fn prologue_signature_covers_the_patch_window() {
        const MINHOOK_PATCH_BYTES: usize = 5;
        assert!(GIVE_ITEMS_PROLOGUE.len() >= MINHOOK_PATCH_BYTES);
    }

    #[test]
    fn the_guard_resolves_its_global_before_arming() {
        assert!(GIVE_ITEMS_GUARD.prepare.is_some());
        publish_world_chr_man_slot(0x1_4000_0000);
        assert_eq!(
            WORLD_CHR_MAN_SLOT.load(Ordering::SeqCst),
            0x1_4000_0000 + WORLD_CHR_MAN_GLOBAL_RVA,
        );
    }

    #[test]
    fn the_guard_is_its_own_group() {
        assert_eq!(
            super::super::REGISTRY
                .iter()
                .filter(|guard| guard.group == GIVE_ITEMS_GUARD.group)
                .count(),
            1
        );
    }
}
