//! The live half of the invade-difficulty setting: what is in force, and when it applies.
//!
//! # Why this is in memory and not in the config file
//!
//! Two reasons, and the player gave the first of them directly on 2026-09-18: "this runtime config
//! that doesn't persist". A difficulty is a decision about the fight you want in the next ten
//! minutes, not a property of your installation, and every restart should start you back at
//! Seamless's own behaviour.
//!
//! The second reason is the shape of the bug this repo already paid for. `widen_to_anywhere` and
//! `widen_band_when_nearby_exhausted` were config keys that could decide how far a search reached,
//! and on 2026-09-18 a file still carrying one from a previous week turned a `Nearby only` search
//! into a whole-population one and dropped the player into two strangers' worlds. Both keys were
//! deleted. A setting that never reaches a file cannot go stale in one, cannot be hand-edited into
//! disagreeing with what the panel shows, and cannot outlive the session that asked for it -- so
//! this is reachable from the settings panel and from nowhere else.
//!
//! # When it applies
//!
//! Only during the far half of `Both near and far`, which begins at exactly one place:
//! `lobby_preflight::hand_over_when_the_neighbourhood_is_empty` calling
//! `local_invasion_filter::hand_off_to_seamless`. Before that the search is this DLL's own, aimed
//! at a ring of tiles at the player's own band; after it, every query is Seamless's, unfiltered,
//! and the band field is the only thing left that decides who can answer.
//!
//! [`in_far_half`] is a latch rather than a question asked of the ring, because the two are read
//! on different sides of one query round. Seamless adds its string filters and only then calls
//! `RequestLobbyList`, where `hunt_target` runs -- so a filter asking the ring what it decided
//! this round would be reading last round's answer. The handover happens on the game task, once,
//! and the latch it sets is true for every query that follows it.

#![cfg(windows)]

use core::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use er_invasion_warp_core::invade_difficulty::InvadeDifficulty;

/// The difficulty in force, as an index into [`InvadeDifficulty::ALL`].
///
/// An index rather than the enum so the whole thing is one relaxed atomic read on the Steam
/// callback thread, which is where the band rewrite happens.
static DIFFICULTY: AtomicUsize = AtomicUsize::new(0);

/// Whether the search has reached the far half, where the difficulty applies.
static FAR_HALF: AtomicBool = AtomicBool::new(false);

/// The difficulty the player has selected.
#[must_use]
pub fn current() -> InvadeDifficulty {
    InvadeDifficulty::from_index(DIFFICULTY.load(Ordering::SeqCst))
}

/// Move to the next difficulty and say which one is now in force.
///
/// The panel's only way in. Cycling wraps, so one button covers all six rows.
pub fn cycle() -> InvadeDifficulty {
    let next = current().next();
    DIFFICULTY.store(next.index(), Ordering::SeqCst);
    next
}

/// Record that the near half is over and the far half has started.
///
/// Called from `hand_off_to_seamless`, which is the single place that hands the running search
/// back to Seamless. Announced once per transition rather than once per process: a player who
/// invades four times in an evening should see the difficulty take effect four times, and a
/// once-per-session line cannot tell the second handover from a handover that did not happen.
pub fn enter_far_half() {
    if FAR_HALF.swap(true, Ordering::SeqCst) {
        return;
    }
    let difficulty = current();
    match difficulty.band_for("0_0") {
        // `0_0` is a stand-in here purely to ask whether this difficulty rewrites anything at all;
        // the real band comes off the query. Saying so plainly beats a line that reports a band
        // nobody will be asked for.
        Some(_) => crate::standalone_log(format_args!(
            "invade-difficulty: the near half is over, so the far half now asks for {} -- {}. \
             Seamless compares this field for equality, so from here only hosts in that bracket \
             can answer any query this search sends.",
            difficulty.label(),
            difficulty.note()
        )),
        None => crate::standalone_log(format_args!(
            "invade-difficulty: the near half is over and the difficulty is {}, so the far half \
             asks for this character's own bracket and nothing here rewrites the query.",
            difficulty.label()
        )),
    }
}

/// Leave the far half, because this search is over.
///
/// Every path that ends a search calls this. A latch left set would apply the difficulty to the
/// opening query of the next invasion -- the near half, at a bracket the player never climbed to
/// -- with nothing on screen to say why nobody nearby could be found.
pub fn leave_far_half() {
    FAR_HALF.store(false, Ordering::SeqCst);
}

/// Whether the running search has reached its far half.
#[must_use]
pub fn in_far_half() -> bool {
    FAR_HALF.load(Ordering::SeqCst)
}

/// The band this query should ask for instead of `own`, or `None` to send Seamless's own value.
///
/// The one place the two halves of the decision meet, so nothing else has to know both. `None` for
/// a search still in its near half, for [`InvadeDifficulty::Default`], and for a value that is not
/// band-shaped.
#[must_use]
pub fn band_for(own: &str) -> Option<String> {
    if !in_far_half() {
        return None;
    }
    current().band_for(own)
}
