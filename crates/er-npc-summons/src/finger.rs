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
//!   permission only where the finger's own red-sign term and `CanStartMultiplay` both pass, so a
//!   place where summoning is barred still refuses it.

#![cfg(windows)]

use core::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicUsize, Ordering};

use er_game_base::mem::{game_rva_for_hook, game_rva_named};
use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};

use crate::addr::{CAN_START_MULTIPLAY, RED_SIGN_TERM};
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
        return usize::from(summoning_allowed_here(player));
    }
    verdict
}

/// `FUN_140656f90` / `WorldChrManImp::CanStartMultiplay`: both `bool(this)`.
type TermFn = unsafe extern "system" fn(usize) -> u8;

/// The last answer [`summoning_allowed_here`] logged: 0 none yet, else `1 + red + 2 * multiplay`.
static LAST_TERMS: AtomicU8 = AtomicU8::new(0);

fn term(rva: u32, what: &'static str, this: usize) -> Option<bool> {
    let address = game_rva_named(rva, what).ok()?;
    // SAFETY: both terms are `bool(this)` predicates the game calls from `CanUseGoods`.
    let call: TermFn = unsafe { core::mem::transmute(address) };
    Some(unsafe { call(this) } != 0)
}

/// Whether the finger may be used here although the game refused it: only where the finger's own
/// red-sign term (the player's red-sign bit, safe position, the world's and the play region's
/// red-sign limits) and `WorldChrManImp::CanStartMultiplay` both pass. That keeps the refusal in
/// Roundtable Hold and every other place summoning is barred, and lifts only the offline refusal.
/// Live 2026-10-06 in the open world under Seamless: both true. The log names the refusing term
/// whenever the answer changes.
fn summoning_allowed_here(player: usize) -> bool {
    let red = term(RED_SIGN_TERM, "RED_SIGN_TERM", player);
    let multiplay = crate::game::world_chr_man()
        .and_then(|wcm| term(CAN_START_MULTIPLAY, "CAN_START_MULTIPLAY", wcm));
    let (Some(red), Some(multiplay)) = (red, multiplay) else {
        if LAST_TERMS.swap(u8::MAX, Ordering::AcqRel) != u8::MAX {
            summons_log(format_args!(
                "finger: goods 101 refused -- a term could not be asked (red-sign term {red:?}, \
                 CanStartMultiplay {multiplay:?})"
            ));
        }
        return false;
    };
    let code = 1 + u8::from(red) + 2 * u8::from(multiplay);
    if LAST_TERMS.swap(code, Ordering::AcqRel) != code {
        let refusing = match (red, multiplay) {
            (true, true) => "none, offered",
            (false, true) => "the red-sign term",
            (true, false) => "CanStartMultiplay",
            (false, false) => "the red-sign term and CanStartMultiplay",
        };
        summons_log(format_args!(
            "finger: goods 101 -- red-sign term {red}, CanStartMultiplay {multiplay}; refused by \
             {refusing}"
        ));
    }
    red && multiplay
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
