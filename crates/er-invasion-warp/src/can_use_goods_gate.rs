//! The vanilla invasion fingers, made usable by answering the gate instead of rewriting the param.
//!
//! # Why not the param row
//!
//! Clearing `disable_offline` on the three finger rows does make them usable, and it also removes
//! the player from every other Seamless player's matchmaking pool. `lobby_key` is
//! `sha256(B + A + SALT32)` where `B` fingerprints the param data the game has loaded -- not
//! `regulation.bin` on disk. Measured 2026-09-16: the rows at their shipped `0x63/0xe3/0x63` give
//! `B = 76DFB8C5A838F5A3`, and cleared to `0x43/0xc3/0x43` they give `B = 76DFB8C5A838F603`, a key
//! advertised and filtered on that nobody else has. The file was byte-identical throughout, so an
//! untouched file proves nothing; only the loaded bytes do. The same thing happened to
//! `er_death_persist` in August, where the symptom was an invasion search that simply never found
//! anybody.
//!
//! So this module writes no param byte. It overrides `CS::CanUseGoods`' return value for the three
//! finger row ids, which settles all four of that function's terms at once.
//!
//! # The two things that make it work
//!
//! Arxan stubs the entry: the live first byte is `jmp rel32` while bytes `5..` still match the
//! image, so a detour at the entry destroys the jump and a hook there catches nothing.
//! `er_hook::register_union_hook7_runtime_derived` follows it.
//!
//! The signature has seven parameters, so a narrower dispatcher would hand the callee garbage in
//! `rightWeaponId`, `leftWeaponId` and `cannotConsumeForRepair`.
//!
//! # What it deliberately does not do
//!
//! It runs the original first and keeps every verdict that allows, so "the item is not held" and
//! "one is already in flight" stay the engine's call. And it leaves the refusal alone while
//! `CSSessionManager->lobbyState` is `Client` -- that is the engine's own fourth term, and it
//! means the player is already in somebody else's world, where a second finger is an accidental
//! double use. It must be `Client` and nothing weaker: during a search the state is `None` or
//! `Joining`, and pressing the finger again there is how the player cancels the search and toggles
//! the mode, in a loop.

use core::sync::atomic::{AtomicUsize, Ordering};

/// `CS::CanUseGoods` on 1.17. Carried from 1.16.2 `0x14068e010` with
/// `scripts/map-rvas-1162-to-1170.py` (delta `+0xe50`, unique 43-byte signature) and recorded in
/// `docs/recon/rva-map-1162-to-1170.verified.tsv`. Below the `0xafefe9` boundary, so 1.17.0 and
/// 1.17.1 agree.
const CAN_USE_GOODS_RVA: usize = 0x68_ee60;

/// The three `EquipParamGoods` rows Seamless greys out, from the decompile's own branch:
/// `uVar31 == 0x66 || uVar31 - 0x6f < 2`.
const BLOODY_FINGER: usize = 102;
const FESTERING_BLOODY_FINGER: usize = 111;
const RECUSANT_FINGER: usize = 112;

/// Trampoline to the original `CanUseGoods`, or the next handler in the union chain.
static ORIG_CAN_USE_GOODS: AtomicUsize = AtomicUsize::new(0);

/// How many refusals this has turned into permissions, and how many it left alone because the
/// player was already connected. Counted separately: one number could not tell "the gate never
/// ran" from "the gate ran and correctly declined".
static FORCED: AtomicUsize = AtomicUsize::new(0);
static LEFT_REFUSED_WHILE_CONNECTED: AtomicUsize = AtomicUsize::new(0);

/// Whether a goods row is one of the three this opens.
fn is_invasion_finger(goods_id: usize) -> bool {
    matches!(
        goods_id,
        BLOODY_FINGER | FESTERING_BLOODY_FINGER | RECUSANT_FINGER
    )
}

/// Whether the engine reports the player is in another world as a client right now.
///
/// `None` when the manager cannot be read, and the caller treats that as "not connected" -- an
/// unreadable manager must not silently re-grey the item, because that failure looks identical to
/// the feature never having worked.
#[cfg(windows)]
fn connected_as_client() -> Option<bool> {
    use er_invasion_warp_core::join_progress::lobby_state;
    use er_invasion_warp_core::warp::{SESSION_LOBBY_STATE_OFFSET, SESSION_MANAGER_GLOBAL_RVA};

    let base = er_game_base::mem::game_module_base().ok()?;
    // SAFETY: fault-tolerant read of a game global through the checked resolver.
    let manager = unsafe {
        er_game_base::mem::safe_read_usize(er_game_base::mem::game_data_addr(
            base,
            SESSION_MANAGER_GLOBAL_RVA,
            "SESSION_MANAGER_GLOBAL_RVA",
        ))
    }?;
    if manager == 0 {
        return None;
    }
    // SAFETY: fault-tolerant read of one int inside the manager the global just named.
    let state = unsafe { er_game_base::mem::safe_read_i32(manager + SESSION_LOBBY_STATE_OFFSET) }?;
    Some(state == lobby_state::CLIENT)
}

/// `CanUseGoods(goodsId, PlayerIns*, SpecialEffect*, CharacterType, rightWeaponId, leftWeaponId,
/// cannotConsumeForRepair)`.
///
/// # Safety
///
/// Game thread, called by the engine. Forwards all seven arguments to whatever `ORIG_CAN_USE_GOODS`
/// holds, which may be the next handler in the union chain rather than the game trampoline, so it
/// is called through [`er_hook::UnionFn7`] and not the game's own signature.
#[cfg(windows)]
unsafe extern "system" fn can_use_goods_hook(
    goods_id: usize,
    player: usize,
    special_effect: usize,
    chr_type: usize,
    right_weapon: usize,
    left_weapon: usize,
    cannot_consume_for_repair: usize,
) -> usize {
    let orig = ORIG_CAN_USE_GOODS.load(Ordering::Acquire);
    if orig == 0 {
        return 0;
    }
    // SAFETY: the slot holds either the game trampoline or the next handler, both `UnionFn7`.
    let original = unsafe { core::mem::transmute::<usize, er_hook::UnionFn7>(orig) };
    // SAFETY: forwarding the engine's own arguments, unaltered.
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

    if verdict != 0 || !is_invasion_finger(goods_id) {
        return verdict;
    }
    if connected_as_client() == Some(true) {
        LEFT_REFUSED_WHILE_CONNECTED.fetch_add(1, Ordering::Relaxed);
        return verdict;
    }
    FORCED.fetch_add(1, Ordering::Relaxed);
    1
}

/// Install the gate override, once.
///
/// Returns whether the hook is in place. A refusal is logged with the reason `er-hook` gave and
/// leaves the fingers greyed -- which is the shipped behaviour, not a broken one.
///
/// # Safety
///
/// Game task thread. Installs one native detour through the shared union.
#[cfg(windows)]
pub unsafe fn install() -> bool {
    static INSTALLED: AtomicUsize = AtomicUsize::new(0);
    if INSTALLED.load(Ordering::SeqCst) != 0 {
        return true;
    }

    let Ok(base) = er_game_base::mem::game_module_base() else {
        return false;
    };
    let entry = base + CAN_USE_GOODS_RVA;
    // SAFETY: a seven-argument handler on a seven-argument target; `ORIG_CAN_USE_GOODS` is the
    // static the handler calls through `UnionFn7`.
    match unsafe {
        er_hook::register_union_hook7_runtime_derived(
            entry,
            can_use_goods_hook as er_hook::UnionFn7,
            &ORIG_CAN_USE_GOODS,
        )
    } {
        Ok(()) => {
            INSTALLED.store(1, Ordering::SeqCst);
            crate::standalone_log(format_args!(
                "vanilla-fingers: CanUseGoods @0x{entry:x} answered, no param byte written -- the \
                 three invasion fingers are usable and lobby_key is untouched"
            ));
            true
        }
        Err(status) => {
            crate::standalone_log(format_args!(
                "vanilla-fingers: CanUseGoods @0x{entry:x} refused: {status:?} -- the fingers stay \
                 greyed"
            ));
            false
        }
    }
}

/// `(forced, left refused while connected)`, for telemetry and tests.
#[must_use]
pub fn counters() -> (usize, usize) {
    (
        FORCED.load(Ordering::Relaxed),
        LEFT_REFUSED_WHILE_CONNECTED.load(Ordering::Relaxed),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_three_invasion_fingers_are_opened() {
        assert!(is_invasion_finger(BLOODY_FINGER));
        assert!(is_invasion_finger(FESTERING_BLOODY_FINGER));
        assert!(is_invasion_finger(RECUSANT_FINGER));
        // The decompile's branch is `uVar31 == 0x66 || uVar31 - 0x6f < 2`, so 0x66, 0x6f and 0x70
        // and nothing adjacent.
        for neighbour in [101usize, 103, 110, 113, 0, 1] {
            assert!(
                !is_invasion_finger(neighbour),
                "{neighbour} is not a finger"
            );
        }
    }

    #[test]
    fn the_row_ids_are_the_decompiles_own_literals() {
        assert_eq!(BLOODY_FINGER, 0x66);
        assert_eq!(FESTERING_BLOODY_FINGER, 0x6f);
        assert_eq!(RECUSANT_FINGER, 0x70);
    }

    /// The whole point of this module, asserted against its own source so it cannot regress
    /// quietly: it must contain no write to the param table.
    ///
    /// A param write is what moves `lobby_key`, and the failure it causes is a search that finds
    /// nobody -- indistinguishable from an empty pool, which is why a compile-time reader is worth
    /// more here than a comment.
    #[test]
    fn this_module_writes_no_param_byte() {
        let source = include_str!("can_use_goods_gate.rs");
        // Comments are stripped first: this module's own prose names `disable_offline` several
        // times to explain why it does not write one, and a scan that cannot tell prose from code
        // would fail on its own documentation.
        let code: String = source
            .split("mod tests {")
            .next()
            .expect("a tests module")
            .lines()
            .filter(|line| !line.trim_start().starts_with("//"))
            .collect::<Vec<_>>()
            .join("\n");
        let body = code.as_str();
        for forbidden in [
            "write_volatile",
            "write_unaligned",
            "as *mut u8",
            "disable_offline",
        ] {
            assert!(
                !body.contains(forbidden),
                "`{forbidden}` in the gate module -- this module answers the predicate and must \
                 never write a param row"
            );
        }
    }

    #[test]
    fn the_regate_is_client_and_nothing_weaker() {
        use er_invasion_warp_core::join_progress::lobby_state;
        let source = include_str!("can_use_goods_gate.rs");
        assert!(source.contains("connected_as_client() == Some(true)"));
        // `Joining` and `None` are the states a search sits in, and the finger must stay pressable
        // there -- pressing it again is how the player cancels and toggles the mode, in a loop.
        assert_ne!(lobby_state::CLIENT, lobby_state::JOINING);
        assert_ne!(lobby_state::CLIENT, lobby_state::NONE);
        assert_eq!(lobby_state::CLIENT, 6);
    }
}
