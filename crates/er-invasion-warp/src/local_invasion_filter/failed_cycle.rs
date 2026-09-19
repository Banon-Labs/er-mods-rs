//! What a failed search cycle is allowed to widen, and in which direction.
//!
//! Split out of `local_invasion_filter` on 2026-09-17 when that file crossed the 3200-line hard
//! limit. The cut is along a real seam: both functions here answer the same question -- Seamless
//! just finished a search cycle and connected to nobody, so what should the next one ask
//! differently? -- and neither judges a match, identifies a session, or drives an action. The two
//! axes they move are deliberately exclusive, which is the reason they are neighbours rather than
//! one function: [`advance_place_on_failed_cycle`] steps the place the search asks about, and
//! [`climb_band_on_failed_cycle`] steps the band it will match, and widening both at once leaves
//! no way to say which one found a host.
//!
//! Both are reached from `log_transition`, which sees every session-state change this module
//! observes. That is the only trigger available: `sweep_tick`'s `Outcome::Empty` was the previous
//! one and it can never fire during a live search, because the sweep only walks while Seamless's
//! session is idle.

#[cfg(windows)]
use core::sync::atomic::{AtomicU32, Ordering};

use super::{FINGER_REACH_NONE, ersc, finger_reach};

/// Step the place ladder one tile each time a connect attempt falls back to searching.
///
/// This is the near half of `Both near and far`, and until now it could not move. The player's
/// own words for what the row owes them, 2026-09-17: "I'm supposed to start by searching for
/// people who share this DLL within this location but once they are exhausted, I'm supposed to
/// use the default seamless lobby connection/key to go find more people." The first clause is the
/// ring; the second is `RingStep::Everywhere`, which `hunt_target` turns into a query carrying no
/// key of ours at all.
///
/// The ring had two things that could move it and neither fires during a live search.
/// `lobby_publish::advance_search_place` is called from the Steam query detour, which Seamless
/// does not re-enter once it is searching, and from `actions.rs`'s own attempt-ended handler,
/// which never ran: run `br-20260918-020159-9b03` logs `the attempt ended without us cancelling
/// it` zero times across twenty-three cycles, because Seamless restarts its own search and this
/// module is never asked. So the log carries exactly one `prefilter: asking for m61_48_45_00
/// (1 of 49)` line and every query after it names that same tile -- a query filtered on
/// `er_invasion_warp_map`, which only hosts running this DLL publish, so the search was pinned to
/// a population of one for its whole life.
///
/// The failed cycle is the one signal that does fire, twenty-three times in that run, and
/// [`climb_band_on_failed_cycle`] directly below already recognises it.
///
/// Where the two rows part is not here but in `advance_ring`, which is the only place that can
/// return `RingStep::Everywhere` and does so only when
/// [`crate::local_invasion_filter::may_widen_to_anywhere`] answers yes -- that is, for `Both near
/// and far` and nothing else. So a spent ring widens for that row and rewinds for `Nearby only`,
/// and this function can step both without either reaching a population the player declined. It
/// used to be a config field, which is how a file came to overrule the row. User directive
/// 2026-09-17,
/// after the `nobody_publishes` branch let the near row through: "I hit all 48 locations before
/// invading in seamless, and this should never happen."
#[cfg(windows)]
pub(super) fn advance_place_on_failed_cycle(abi: &ersc::Abi, previous: usize, state: u32) {
    if state != abi.state_searching {
        return;
    }
    let previous = previous as u32;
    if previous == usize::MAX as u32
        || previous == abi.state_idle
        || previous == abi.state_searching
    {
        return;
    }
    // Both rows, and `Nearby only` is the one that needs it most. Its ring cannot widen whatever
    // this does: `may_widen_to_anywhere` is false for that row, so `advance_ring` takes its
    // `None =>` arm and rewinds rather than returning `Everywhere`.
    // Stepping it here is the difference between a ring that rotates through the neighbourhood
    // and one frozen on tile 1 of 49, which is what every other caller leaves it at during a live
    // search.
    if finger_reach() == FINGER_REACH_NONE {
        return;
    }
    // The sweep is the fast way to the same answer and outranks this when it has one. It asks
    // Steam about every place directly, at about 138ms each, so a ring it was able to walk is
    // finished in seconds rather than in one tile per fifteen-second cycle. It can only walk
    // while the session is idle, which is ordinary play before the item is used; this ladder is
    // what carries the near half when the item was used before that walk could finish.
    //
    // Outranking it is not owning it. The sweep's answer is a reading taken during ordinary play,
    // and `hunt_target` narrows every query to the block it names for as long as it stands, so a
    // host who has since closed their world pins the whole search to a tile nobody is in -- and
    // the sweep cannot correct itself, because it marks that answer finished and only walks while
    // the session is idle. Counting the cycles that fail against it is what turns the reading back
    // into a lead: after `CYCLES_BEFORE_THE_SWEEP_IS_STALE` of them the sweep is dropped,
    // `nearby()` answers `Idle` again, and the ring below resumes from where it was.
    if matches!(
        crate::lobby_preflight::nearby(),
        crate::lobby_preflight::Nearby::Found(_) | crate::lobby_preflight::Nearby::Empty(_)
    ) {
        let failed = CYCLES_AGAINST_THE_SWEEP.fetch_add(1, Ordering::SeqCst) + 1;
        if failed < CYCLES_BEFORE_THE_SWEEP_IS_STALE {
            return;
        }
        CYCLES_AGAINST_THE_SWEEP.store(0, Ordering::SeqCst);
        crate::lobby_preflight::clear_sweep();
        crate::standalone_log(format_args!(
            "sweep: {failed} search cycle(s) aimed at the place the sweep found have connected to \
             nobody, so that answer is dropped and the search walks its ring again. A sweep hit is \
             a reading taken while the session was idle; a host who has closed their world since \
             would otherwise hold every query on one tile for as long as the finger is held."
        ));
        return;
    }
    CYCLES_AGAINST_THE_SWEEP.store(0, Ordering::SeqCst);
    crate::lobby_publish::advance_search_place();
}

/// How many failed cycles a sweep hit is worth before the search stops aiming at it.
///
/// A cycle is roughly Seamless's fifteen-second connect attempt, so this is about a minute of
/// asking one place. Long enough that a host who is simply slow to answer is not abandoned on the
/// first miss, short enough that a stale hit does not own the rest of the session.
#[cfg(windows)]
const CYCLES_BEFORE_THE_SWEEP_IS_STALE: u32 = 4;

/// Failed cycles since the sweep's answer was last believed.
#[cfg(windows)]
static CYCLES_AGAINST_THE_SWEEP: AtomicU32 = AtomicU32::new(0);

/// Climb one matchmaking band each time a connect attempt falls back to searching.
///
/// The band ladder used to hang off the neighbourhood sweep reporting its ring empty, and that
/// trigger can never fire: `sweep_tick` only advances while the Seamless session is idle, and a
/// live invasion search never is. Measured twice, on `br-20260918-005917-483b` and again on
/// `br-20260918-015127-6a6f` after the ladder shipped -- the log carries exactly one
/// `prefilter: asking for ... (1 of 49)` line per run and every query thereafter names that same
/// place, so `Outcome::Empty` is unreachable and so was everything behind it.
///
/// A search cycle is the signal that does fire. `0x0e SEARCHING -> 0x0f -> 0x12 -> 0x0e` is
/// Seamless asking Steam, matching nothing it can connect to, and starting over; the return leg is
/// one exhausted attempt at the current band, roughly every fifteen seconds. Counting those is
/// what "after exhausting nearby" has to mean while the ring cannot walk.
///
/// The transition is recognised structurally rather than by naming `0x12`, which is unreversed:
/// any state that is neither idle nor searching falling back to searching is a cycle that did not
/// connect. That also covers the `0x0f` path, which is the same failure a beat earlier.
#[cfg(windows)]
pub(super) fn climb_band_on_failed_cycle(abi: &ersc::Abi, previous: usize, state: u32) {
    if state != abi.state_searching {
        return;
    }
    let previous = previous as u32;
    if previous == usize::MAX as u32
        || previous == abi.state_idle
        || previous == abi.state_searching
    {
        return;
    }
    // One question, one answer: `may_climb_band` is true for `Nearby only` and for nothing else.
    // `Both near and far` has its own rung -- dropping the location filter -- and climbing bands
    // underneath it would widen two axes at once with no way to say which one found a host; a
    // search with no row behind it takes no rung at all. This used to be two tests, a reach check
    // and a `widen_band_when_nearby_exhausted` config read, and the config half is what let a file
    // disagree with the row the player picked.
    if !crate::local_invasion_filter::may_climb_band() {
        return;
    }
    let (rung, restarted) = crate::lobby_publish::climb_band();
    crate::lobby_publish::allow_band_climb_notice();
    if restarted {
        crate::standalone_log(format_args!(
            "band-ladder: every rung has been asked and no cycle connected, so the ladder starts \
             over at this character's own band and climbs again. Staying on the top rung, which \
             is what it used to do, meant a search left running kept asking the band furthest \
             from the player and could never return somebody who turned up at their own."
        ));
        return;
    }
    crate::standalone_log(format_args!(
        "band-ladder: that search cycle connected to nobody, so the next one asks one band higher \
         -- +{} weapon band(s), +{} level band(s) above this character's own. Seamless matches its \
         `<level>_<weapon>` value for equality, so a host one band away answers no query at all \
         and looks exactly like an empty world.",
        rung.weapon_steps, rung.level_steps
    ));
}

/// Host-side stub: the ring and the band ladder both live behind `lobby_publish`'s live half,
/// which only exists on the target. The decision halves these two drive are tested there.
#[cfg(not(windows))]
pub(super) fn advance_place_on_failed_cycle(_abi: &ersc::Abi, _previous: usize, _state: u32) {}

/// Host-side stub, for the same reason as its sibling directly above.
#[cfg(not(windows))]
pub(super) fn climb_band_on_failed_cycle(_abi: &ersc::Abi, _previous: usize, _state: u32) {}

// The ladder no longer runs out, so the latch that printed its one spent line is gone with it: a
// lap boundary is an event worth a line every time it happens, not once per session.
