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

/// `CS::PlayerIns::CanUseBreakInItem` on 1.17 -- the engine's own answer to "may this player use
/// an invasion item right now", and the term this module must never override.
///
/// Carried from 1.16.2 `0x140656f00` with `scripts/map-rvas-1162-to-1170.py`: delta `+0xe50`,
/// unique on a 38-byte signature, the same delta `CAN_USE_GOODS_RVA` took. Below the `0xafefe9`
/// boundary, so 1.17.0 and 1.17.1 agree. The mapping was then read rather than trusted -- the
/// disassembly at `0x140657d50` in `eldenring-deobf-1.17.1.bin` is instruction-for-instruction the
/// 1.16.2 function, differing only in the rip displacements the moved globals force, and it ends
/// in a `ret` at `+0x8d` for the 142 bytes Ghidra reports.
///
/// What it decides, from the decompile:
///
/// ```text
/// CanUseBreakInItem(player) = IsInSafePosRange(player)
///                          && WorldChrManImp::CanStartBreakIn(WorldChrMan)
///                          && IsBreakInLimitedByEventFlagId(&player->playRegionId)
///
/// CanStartBreakIn(w)       = w->mainPlayerIns != null
///                          && !HasSpecialEffectWithStateInfo(player->specialEffect, 0x1a2)
///                          && IsBreakInLimitedByEventFlagId(FieldArea->playRegionParamId)
/// ```
///
/// State info `0x1a2` is NOT the world-open bit and must not be described as one: it appears in
/// `CanStartMultiplay` and `CanStartBreakIn` alike, in byte-identical position, so it gates both
/// halves of multiplayer rather than picking out a host. What it actually is has not been read.
///
/// The reason to ask the engine at all rather than a lobby key is that Seamless 2.0.1 no longer
/// spells its lobby-data key names in plaintext. `lobby_breakin_lobby_ykssr_199_6` -- the key
/// `docs/invasion-warp-second-player-setup.md` records as reading `true` for an open world -- along
/// with `breakin`, `ykssr` and `lobby_type`, occurs zero times in
/// `vendor-archive/seamless/ersc-2.0.1.dll`. That doc's measurement was taken against an older
/// build, so the key cannot be named statically today and an engine predicate is the firmer source.
const CAN_USE_BREAK_IN_ITEM_RVA: u32 = 0x65_7d50;

/// `CS::GetPartyMemberInfo` on 1.17 -- `return GLOBAL_GameMan->partyMemberInfo`, 15 bytes.
///
/// Read out of the 1.17.1 image rather than mapped: `CanUseGoods` calls it, and the instruction at
/// `0x14068effc` is `call 0x14067b120` where 1.16.2's `0x14068e1ac` is `call 0x14067a2d0`.
const GET_PARTY_MEMBER_INFO_RVA: u32 = 0x67_b120;

/// `CS::PartyMemberInfo::HasNonNPCPhantoms` on 1.17 -- "are there other real players in my world".
///
/// ```text
/// HasNonNPCPhantoms(p) = p->sessionPlayerCount >= 2
///                     || SummoningFrame::HasNonNPCPhantoms(frame over CSEventMan's SosSignMan)
/// ```
///
/// Also read out of the image, and it had to be: this one moved `+0x12e0` while everything else
/// around it moved `+0xe50`, so `scripts/map-rvas-1162-to-1170.py` returned `UNRESOLVED -- 143
/// shape matches`. A `__security_check_cookie` prologue is not distinctive enough to sign. The
/// call site settles it without signatures -- 1.16.2 `0x14068e1b4` is `call 0x1409f93c0` and
/// 1.17.1 `0x14068f004` is `call 0x1409fa6a0`, in an instruction stream otherwise byte-identical
/// for the twenty bytes either side.
const PARTY_HAS_NON_NPC_PHANTOMS_RVA: u32 = 0x9f_a6a0;

/// `bool CanUseBreakInItem(PlayerIns *player)` -- one argument in `rcx`, `al` out.
type CanUseBreakInItemFn = unsafe extern "system" fn(usize) -> bool;

/// `PartyMemberInfo *GetPartyMemberInfo(void)`.
type GetPartyMemberInfoFn = unsafe extern "system" fn() -> usize;

/// `bool HasNonNPCPhantoms(PartyMemberInfo *info)`.
type HasNonNpcPhantomsFn = unsafe extern "system" fn(usize) -> bool;

/// Trampoline to the original `CanUseGoods`, or the next handler in the union chain.
static ORIG_CAN_USE_GOODS: AtomicUsize = AtomicUsize::new(0);

/// How many refusals this has turned into permissions, and how many it left alone because the
/// player was already connected. Counted separately: one number could not tell "the gate never
/// ran" from "the gate ran and correctly declined".
static FORCED: AtomicUsize = AtomicUsize::new(0);
static LEFT_REFUSED_WHILE_CONNECTED: AtomicUsize = AtomicUsize::new(0);
/// Refusals kept because the engine's own break-in predicate said no -- a safe position, or a
/// region where invading is flagged off. Separate from the two above so one run can say which term
/// spoke: a finger that has gone dead everywhere would show this climbing while `FORCED` stays at
/// zero, and that is a different bug from the hook never installing.
static LEFT_REFUSED_BY_BREAK_IN_TERM: AtomicUsize = AtomicUsize::new(0);
/// Refusals kept because the player's own world has other real players in it.
static LEFT_REFUSED_WHILE_HOSTING: AtomicUsize = AtomicUsize::new(0);

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

/// Ask the engine whether this player may use a break-in item at all.
///
/// `None` when nothing can be read or resolved, and every caller treats that as "do not let this
/// decide". `CanStartBreakIn` dereferences `GLOBAL_WorldChrMan` through `FD4Singleton`, which
/// `DLPanic`s on null rather than returning, so the global is checked here first: `CanUseGoods`
/// reaches the call only past `CanStartMultiplay`, and this module calls it on frames where that
/// term refused, which the engine itself never does.
#[cfg(windows)]
fn can_use_break_in_item(player: usize) -> Option<bool> {
    if player == 0 {
        return None;
    }
    let base = er_game_base::mem::game_module_base().ok()?;
    // SAFETY: fault-tolerant read of a game global through the checked resolver.
    let world_chr_man = unsafe {
        er_game_base::mem::safe_read_usize(er_game_base::mem::game_data_addr(
            base,
            er_game_base::rva::WORLD_CHR_MAN_GLOBAL_RVA,
            "WORLD_CHR_MAN_GLOBAL_RVA",
        ))
    }?;
    if world_chr_man == 0 {
        return None;
    }
    let entry = er_game_base::mem::game_rva_for_hook(CAN_USE_BREAK_IN_ITEM_RVA).ok()?;
    // SAFETY: a one-argument predicate at an address this build mapped, called on the game thread
    // the engine already calls it from. It reads three fields and calls two further predicates; it
    // writes nothing and cannot re-enter `CanUseGoods`.
    let f = unsafe { core::mem::transmute::<usize, CanUseBreakInItemFn>(entry) };
    Some(unsafe { f(player) })
}

/// Host-side stub: there is no engine to ask.
#[cfg(not(windows))]
fn can_use_break_in_item(_player: usize) -> Option<bool> {
    None
}

/// Whether the player's own world currently holds other real players.
///
/// `None` when either address cannot be mapped, and the caller treats that as "do not let this
/// decide". `GetPartyMemberInfo` dereferences `GLOBAL_GameMan` without a null check of its own, so
/// a zero return is taken as unreadable rather than passed on.
#[cfg(windows)]
fn world_has_other_players() -> Option<bool> {
    let get_info = er_game_base::mem::game_rva_for_hook(GET_PARTY_MEMBER_INFO_RVA).ok()?;
    let has_phantoms = er_game_base::mem::game_rva_for_hook(PARTY_HAS_NON_NPC_PHANTOMS_RVA).ok()?;
    // SAFETY: a nullary getter at an address this build mapped, on the game thread the engine
    // calls it from. It reads one field of a singleton and writes nothing.
    let info = unsafe { core::mem::transmute::<usize, GetPartyMemberInfoFn>(get_info)() };
    if info == 0 {
        return None;
    }
    // SAFETY: a one-argument predicate over the pointer the getter just returned. It builds a
    // stack `SummoningFrame`, reads it, destroys it, and writes nothing the caller owns.
    Some(unsafe { core::mem::transmute::<usize, HasNonNpcPhantomsFn>(has_phantoms)(info) })
}

/// Host-side stub: there is no party to count.
#[cfg(not(windows))]
fn world_has_other_players() -> Option<bool> {
    None
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
    // The player's own world being open to other players is the engine's call, not this module's.
    //
    // `CanUseGoods` reaches `CanUseBreakInItem` only after `CanStartMultiplay` has already passed,
    // and under Seamless that outer term is what refuses -- which is the refusal this override
    // exists to lift. Lifting it lifted the inner one with it, because a hook on the return value
    // cannot see which of the four terms produced the zero. So the inner term is asked directly,
    // and its `false` is kept: a host whose world is open may not press a Bloody Finger, exactly as
    // vanilla decides it.
    //
    // An unreadable answer forces anyway, for the same reason `connected_as_client` does: a read
    // that fails looks identical to a feature that never worked, and re-greying the item on a
    // failed read would hide this module rather than gate it.
    if can_use_break_in_item(player) == Some(false) {
        LEFT_REFUSED_BY_BREAK_IN_TERM.fetch_add(1, Ordering::Relaxed);
        return verdict;
    }
    // A world open to other players is not a world to invade out of.
    //
    // `CanUseBreakInItem` above covers the flag and region side; this covers the population side,
    // and they are genuinely different questions -- the engine asks both, in different places, and
    // `HasNonNPCPhantoms` is the one that notices a co-op partner standing next to you. Being in
    // co-op is one of the two ways `docs/invasion-warp-second-player-setup.md` records a world
    // becoming invadable, so a player whose own world is invadable does not get to invade.
    //
    // Unreadable forces, as above.
    if world_has_other_players() == Some(true) {
        LEFT_REFUSED_WHILE_HOSTING.fetch_add(1, Ordering::Relaxed);
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

    // `game_rva_for_hook`, not `base + rva`, and not `game_rva` either.
    //
    // Raw arithmetic is what `check-stale-rva-calls.py` and `audit-1170-gate-bypass.py` both flag
    // here, and they are right to: `CAN_USE_GOODS_RVA` is a 1.16.2 address and nothing in
    // `base + rva` asks whether it still means anything on the running build. It happens to be
    // correct on 1.17.1 -- run br-20260917-031812-b399 logged `CanUseGoods @0x14068ee60 answered`
    // -- which is evidence about today's build, not about the next one, and the failure would be
    // silent.
    //
    // The hook helper and not `game_rva` because the address is about to be handed to
    // `register_union_hook7_runtime_derived`, which owns the single resolve. A second one is not
    // merely redundant: `game_rva_for_hook`'s own doc records three detours installed on the wrong
    // function that way, with no error and no log line. An address this build cannot map is
    // refused inside the hook API instead, as `HOOK REFUSED`.
    let Ok(entry) = er_game_base::mem::game_rva_for_hook(CAN_USE_GOODS_RVA as u32) else {
        return false;
    };
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
