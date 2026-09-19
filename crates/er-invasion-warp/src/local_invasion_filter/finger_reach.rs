//! Which reach a vanilla invasion finger asked for, and what that choice forces on the config.
//!
//! Three states and the predicates over them, split out of `local_invasion_filter` because that
//! file crossed the hard size limit in `scripts/check-rust-file-sizes.py`. Nothing else moved
//! with it: the reach is one `AtomicUsize` and the functions here are the only readers and the
//! only writer, so the module boundary costs nothing and the callers keep their spelling through
//! the re-export beside `mod finger_reach`.

use std::sync::atomic::{AtomicUsize, Ordering};

use er_invasion_warp_core::local_invasion::LocalInvasionConfig;

/// Which reach a vanilla invasion finger asked for, or `FINGER_REACH_NONE`.
///
/// An in-memory override rather than a write to the player's file, for two reasons the user gave
/// directly: using an item must not edit their settings, and it must not hinge on what those
/// settings happen to be. A finger whose behaviour depends on `search_by_location` being on is a
/// finger that does nothing for most players, silently -- which is exactly the failure the config
/// line above already warns about for a bare `search_radius`.
static FINGER_REACH: AtomicUsize = AtomicUsize::new(FINGER_REACH_NONE);
pub(crate) const FINGER_REACH_NONE: usize = 0;
pub(crate) const FINGER_REACH_NEARBY: usize = 1;
pub(crate) const FINGER_REACH_NEAR_AND_FAR: usize = 2;

/// Whether the finger's popup chose `Both near and far`, which must not narrow the lobby query.
pub(crate) fn finger_reach_is_near_and_far() -> bool {
    FINGER_REACH.load(Ordering::SeqCst) == FINGER_REACH_NEAR_AND_FAR
}

/// Whether this search may end by dropping the location filter and asking the whole population.
///
/// The single answer to that question, and deliberately not a config read. Two file keys used to
/// decide it -- `widen_to_anywhere` and `widen_band_when_nearby_exhausted` -- and a file could
/// therefore disagree with the row the player picked in the bounds popup. On 2026-09-18 one did:
/// with `widen_to_anywhere = true` a search the player started as `Nearby only` exhausted its ring,
/// dropped the filter and landed them in a stranger's world in another region, twice. Both keys are
/// deleted and this is what replaced them.
///
/// `Both near and far` alone, so `FINGER_REACH_NONE` answers no: a search with no row behind it --
/// the Challenger's Lynchpin, whose item raises no bounds popup -- gets the narrow promise rather
/// than the wide one, because nobody chose the wide one.
pub(crate) fn may_widen_to_anywhere() -> bool {
    FINGER_REACH.load(Ordering::SeqCst) == FINGER_REACH_NEAR_AND_FAR
}

/// Whether this search may climb to a higher matchmaking band once nearby answers nothing.
///
/// `Nearby only` alone, and it is the rung that row has instead of widening: Seamless compares its
/// `<level band>_<weapon band>` pair for equality, so a host one weapon-upgrade band away is not
/// merely harder to find -- the query cannot return her, and the result is indistinguishable from
/// an empty world. Measured 2026-09-18: a host publishing `2_1` answered nothing while this client
/// asked `0_0`, and the first query that asked `2_1` returned her at index 0.
///
/// Not for `Both near and far`, which has the wider rung above; climbing a band and dropping the
/// location filter at the same moment would move two axes at once and leave nobody able to say
/// which found the host. Not for `FINGER_REACH_NONE` either -- a search nobody scoped takes no
/// widening rung of any kind.
pub(crate) fn may_climb_band() -> bool {
    FINGER_REACH.load(Ordering::SeqCst) == FINGER_REACH_NEARBY
}

/// Whether the finger's popup chose `Nearby only`, which must never produce an unfiltered query.
///
/// This is not the negation of [`finger_reach_is_near_and_far`]: `FINGER_REACH_NONE` is a third
/// state and it means no finger started this search at all, so the map-pin and config-driven paths
/// keep whatever behaviour they had. Only the row that promised the player "nearby" is bound by it.
///
/// The hole it closes, measured live on run `br-20260917-183537-0445`: the player used a Bloody
/// Finger with `Nearby only` while standing in block `0x3d302d00`, the pre-flight found no host
/// anywhere publishing a block id, and `hunt_target`'s `NobodyPublishes` short-circuit returned
/// `None` -- no filter -- so Seamless matched `0x0a000000` and the invasion landed in a different
/// map. That short-circuit is a real optimisation for `Both near and far`, whose second phase is
/// meant to be unfiltered; for `Nearby only` there is no second phase to widen into, and an
/// unfiltered query is not a faster way to search nearby, it is a different search.
pub(crate) fn finger_reach_is_nearby_only() -> bool {
    FINGER_REACH.load(Ordering::SeqCst) == FINGER_REACH_NEARBY
}

/// Record what the finger's popup chose. Cleared by [`stand_down_hunt`] with everything else.
pub(crate) fn set_finger_reach(reach: usize) {
    FINGER_REACH.store(reach, Ordering::SeqCst);
}

/// The raw reach, for the one caller that has to tell all three states apart.
///
/// The two predicates beside this each collapse the other two states into `false`, which is what
/// their callers want and is wrong for `hunt_target`: it has to distinguish "the player chose a
/// reach" from "no finger started this search" before it may narrow anything, and both predicates
/// answer `false` to the second case and to one of the first two.
pub(crate) fn finger_reach() -> usize {
    FINGER_REACH.load(Ordering::SeqCst)
}

/// Overlay the finger's choice on the loaded config, for as long as its search is running.
///
/// The three switches are forced together because they are one mechanism: the widening search runs
/// inside the lobby-query detour, so it needs `steam_hooks`; the tile it starts from comes from
/// `hunt_filter_value`, which answers `None` while `hunt` is off; and the filter judges nothing at
/// all while `enabled` is false. Setting the radius without them is the silent no-op this module
/// already logs a warning about.
pub(super) fn apply_finger_override(mut config: LocalInvasionConfig) -> LocalInvasionConfig {
    let reach = FINGER_REACH.load(Ordering::SeqCst);
    if reach == FINGER_REACH_NONE {
        return config;
    }
    config.enabled = true;
    config.steam_hooks = true;
    // `hunt` is deliberately not forced, and forcing it was the defect. Its own doc comment on
    // `LocalInvasionConfig` says what it costs -- "hunt asks Steam for a key only this DLL's users
    // publish, so while it is on a host without the DLL is invisible to you. That is a trade the
    // user must choose, never a default" -- and this function turned it on for every finger, which
    // made it exactly a default.
    //
    // What it cost, measured with `scripts/er-lobby-search-proof.py`, which sends the queries
    // itself and reads every key of every lobby that comes back. Run `br-20260917-230843-fc3b`
    // with this DLL loaded, and the control run with it withheld, agree:
    //
    // ```text
    // unfiltered control            matching=50
    // seamless shape                matching=50
    // exists: any block id at all   matching=1
    // exists: a key nobody carries  matching=0     <- negative control
    // ```
    //
    // Fifty Seamless hosts reachable; one of them ran this DLL. So every search a finger started
    // was narrowed to that one, and the player watched `Found a host in Highroad Cross` followed
    // by no invasion, over and over, for as long as the finger stayed armed.
    //
    // The other two switches stay forced because they cost the player nothing. `enabled` arms the
    // reject filter, which declines destinations it does not want and still sees every host --
    // that is how a reach is meant to be enforced. `steam_hooks` installs the detour that path
    // rides on. Only `hunt` trades reach away, so only `hunt` is the player's to spend.
    // The player's own radius still decides how wide "nearby" is; the choice only decides whether
    // the search may stop being nearby. A file with no radius set would otherwise make `Nearby
    // only` a single-tile search, which is an empty search.
    if config.prefilter_radius == 0 {
        config.prefilter_radius = 1;
    }
    config
}
