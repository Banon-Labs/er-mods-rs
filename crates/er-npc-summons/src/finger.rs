//! The Duelist's Furled Finger, turned into "pick an NPC to duel".
//!
//! Static RE, 1.16.2 names carried to 1.17.1 (bd `duelist-finger-use-path-1171-2026-10-06`):
//! goods 101 applies SpEffect 10, the "request a red sign" flag; `PlayerIns::UpdateMultiplayData`
//! sees it and calls `PlayerIns::StartMultiplayProcedureWithMountData(player, MultiplayType)` with
//! type 2 (`RED_SUMMON`), which builds the player's sign and sends the request to the server.
//! Goods 100 (Tarnished's Furled Finger) arrives with type 0 and invasion fingers never with 2.
//!
//! Two hooks:
//!
//! * `StartMultiplayProcedureWithMountData`: type 2 while duels are enabled does not run the
//!   original (no sign is built, nothing is sent) and raises [`take_finger_use`] for the game task,
//!   which opens the picker. Every other type runs the original untouched.
//! * `CanUseGoods`, through the shared seven-argument union (`er-invasion-warp` answers the same
//!   function for the invasion fingers): row 101 carries `disable_offline`, so offline it is
//!   refused on `IsInOnlineMode`. A refusal of 101 while duels are enabled is turned into a
//!   permission. That also lifts the item's other terms (safe position, red signs allowed in the
//!   region, no pending request); the duel code re-checks what it needs before it spawns.

#![cfg(windows)]

use core::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_game_base::mem::game_rva_for_hook;
use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};

use crate::log::summons_log;

/// `PlayerIns::StartMultiplayProcedureWithMountData(PlayerIns*, MultiplayType)`, 1.16.2
/// `0x1406587b0`, 1.17.1 `0x140659600` (`IDENTICAL-WHOLE`, recorded in the verified ledger).
const START_MULTIPLAY_PROCEDURE: u32 = 0x0065_87b0;
/// `CS::CanUseGoods`, 1.17.x `0x14068ee60` (1.16.2 `0x14068e010`, `IDENTICAL-WHOLE` in the
/// verified ledger). The hook API audits a runtime-derived entry against the running image's own
/// function table, so it takes the 1.17 address, as `er-invasion-warp` does; the 1.16.2 one is
/// refused with `MH_ERROR_UNSUPPORTED_FUNCTION`.
const CAN_USE_GOODS: u32 = 0x0068_ee60;

/// `MultiplayType` of the Duelist's Furled Finger.
const RED_SUMMON: u8 = 2;
/// `EquipParamGoods` row of the Duelist's Furled Finger.
const DUELIST_FURLED_FINGER: usize = 101;

static DUELS_ENABLED: AtomicBool = AtomicBool::new(false);
static FINGER_USED: AtomicBool = AtomicBool::new(false);
static ORIG_START: AtomicUsize = AtomicUsize::new(0);
static ORIG_CAN_USE_GOODS: AtomicUsize = AtomicUsize::new(0);

type StartFn = unsafe extern "system" fn(usize, u8);

/// Follow the config's `[duel] enabled`.
pub(crate) fn set_enabled(enabled: bool) {
    DUELS_ENABLED.store(enabled, Ordering::Release);
}

/// Whether the finger was used since the last call.
pub(crate) fn take_finger_use() -> bool {
    FINGER_USED.swap(false, Ordering::AcqRel)
}

unsafe extern "system" fn start_multiplay_detour(player: usize, multiplay_type: u8) {
    if multiplay_type == RED_SUMMON && DUELS_ENABLED.load(Ordering::Acquire) {
        FINGER_USED.store(true, Ordering::Release);
        return;
    }
    let original = ORIG_START.load(Ordering::Acquire);
    if original != 0 {
        // SAFETY: the trampoline of the function this replaces.
        let original: StartFn = unsafe { core::mem::transmute(original) };
        unsafe { original(player, multiplay_type) };
    }
}

/// `CanUseGoods(goodsId, player, ...)`, seven integer arguments, called through the union chain.
unsafe extern "system" fn can_use_goods_hook(
    goods_id: usize,
    player: usize,
    special_effect: usize,
    chr_type: usize,
    right_weapon: usize,
    left_weapon: usize,
    cannot_consume_for_repair: usize,
) -> usize {
    let original = ORIG_CAN_USE_GOODS.load(Ordering::Acquire);
    if original == 0 {
        return 0;
    }
    // SAFETY: the slot holds the game trampoline or the next union handler, both `UnionFn7`.
    let original = unsafe { core::mem::transmute::<usize, er_hook::UnionFn7>(original) };
    let verdict = unsafe {
        original(
            goods_id,
            player,
            special_effect,
            chr_type,
            right_weapon,
            left_weapon,
            cannot_consume_for_repair,
        )
    };
    if verdict == 0 && goods_id == DUELIST_FURLED_FINGER && DUELS_ENABLED.load(Ordering::Acquire) {
        return 1;
    }
    verdict
}

/// Install both hooks. Called once, from the install thread.
pub(crate) fn install() {
    // SAFETY: MinHook init is idempotent across DLLs.
    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            summons_log(format_args!("finger: MH_Initialize failed: {status:?}"));
            return;
        }
    }
    match game_rva_for_hook(START_MULTIPLAY_PROCEDURE) {
        Ok(target) => {
            // SAFETY: `start_multiplay_detour` has the target's signature.
            match unsafe {
                MhHook::new(target as *mut c_void, start_multiplay_detour as *mut c_void)
            } {
                Ok(hook) => {
                    ORIG_START.store(hook.trampoline() as usize, Ordering::Release);
                    let applied = unsafe { hook.queue_enable() }.is_ok()
                        && unsafe { MH_ApplyQueued() } == MH_STATUS::MH_OK;
                    // `MhHook` has no `Drop`: the detour stays installed for the process lifetime.
                    summons_log(format_args!(
                        "finger: StartMultiplayProcedureWithMountData @0x{target:x} hooked: {applied}"
                    ));
                }
                Err(status) => summons_log(format_args!(
                    "finger: StartMultiplayProcedureWithMountData @0x{target:x} refused: \
                     {status:?}; the Duelist's Furled Finger keeps its vanilla behaviour"
                )),
            }
        }
        Err(why) => summons_log(format_args!("finger: no game module: {why}")),
    }
    match game_rva_for_hook(CAN_USE_GOODS) {
        // SAFETY: a seven-argument handler on the seven-argument target; the handler calls the
        // slot through `UnionFn7`.
        Ok(entry) => match unsafe {
            er_hook::register_union_hook7_runtime_derived(
                entry,
                can_use_goods_hook as er_hook::UnionFn7,
                &ORIG_CAN_USE_GOODS,
            )
        } {
            Ok(()) => summons_log(format_args!(
                "finger: CanUseGoods @0x{entry:x} answered for goods 101 while duels are enabled"
            )),
            Err(status) => summons_log(format_args!(
                "finger: CanUseGoods @0x{entry:x} refused: {status:?}; offline the finger stays \
                 greyed"
            )),
        },
        Err(why) => summons_log(format_args!("finger: no game module: {why}")),
    }
}
