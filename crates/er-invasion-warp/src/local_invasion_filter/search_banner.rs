//! The list of places a nearby search is asking about, paced so a player can read it.
//!
//! # The gap this closes
//!
//! `banner::announce_prefilter_step` says one place per call and is called from inside the
//! lobby-query detour, so the screen only names a tile when a `RequestLobbyList` goes out. Run
//! br-20260916-233426-b38b is what that looks like from the player's chair: the banner said the
//! search was nearby, the log said `prefilter: asking for m60_52_53_00 (1 of 49)` exactly once,
//! and no place was ever named on screen again. The user's words were "I got a banner saying I was
//! attempting nearby, but I did not get a display for which locations I was searching."
//!
//! The ring is known the moment the search is armed -- it is arithmetic on the block the player is
//! standing in, not something the query discovers. So the places are queued up front and drained
//! here on the game task. A search that asks for all of them at once still reads as a list going
//! past, which is what was asked for; buffering is the point rather than a compromise.
//!
//! # Why the pacing is a parameter and not a sleep
//!
//! This drains on the game task, where a sleep would stall the frames it is waiting for. The clock
//! is read by the caller and passed in, which also makes every rule here testable on the host with
//! no game and no clock of its own.

use std::collections::VecDeque;
use std::sync::Mutex;

/// How long one place stays on screen before the next replaces it.
///
/// A tenth of a second, asked for after a second was tried in game: "speed up the intervals
/// between locations being shown in the banner so its 0.1s per display". The first value was
/// picked so each name could be read; watching it, the player wanted the list, not the reading.
/// The whole ring at its widest now goes past in about five seconds rather than most of a minute,
/// which matters beyond legibility -- the recital is what tells them the nearby half of the search
/// is over, so a recital that outlasts the search reports a phase that has already ended.
pub(crate) const STEP_INTERVAL_MS: u64 = 100;

/// A cap on how many places may be queued at once.
///
/// `search_ring::MAX_RADIUS` is 3, which is 49 places, so this is the ring at its widest plus
/// room. It exists so a caller that queues in a loop cannot turn the banner into a recital nobody
/// can sit through, and so the memory this holds is bounded by construction.
pub(crate) const MAX_QUEUED: usize = 64;

/// One place the search is about to ask for, and where it sits in the ring.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Step {
    /// The block id, spelled for the player by `place_name::place_name_for_block`.
    pub(crate) block: u32,
    /// Which step this is, starting at 1, so the banner can say "3 of 9".
    pub(crate) ordinal: usize,
    /// How many places the ring holds.
    pub(crate) total: usize,
}

/// The recital's whole state: what is left to say, when the last line went up, whether the ring
/// starts over when it runs out, and the ring to refill from when it does.
///
/// The repeat flag exists because the two rows want opposite things from an exhausted ring. `Both
/// near and far` wants it to end: the recital finishing is how the player is told the near half is
/// over and the far half has begun. `Nearby only` has no far half, so for that row the end of the
/// ring is the start of the next lap -- "When I exhausted nearby, the banner didn't come up again
/// saying it was going through 1-N locations again for a new player. It should keep doing this on
/// repeat until I cancel" (user, live, 2026-09-17).
///
/// The search itself was never the problem and does not need restarting: run
/// br-20260917-193816-8621 shows it cycling `0x12 -> 0x0e SEARCHING -> 0x0f -> 0x12` for as long as
/// the finger stays armed. Only the recital died, because [`pump`] popped and nothing refilled.
#[derive(Default)]
struct Queue {
    pending: VecDeque<Step>,
    last_shown: u64,
    repeat: bool,
    /// The ring as queued, so a lap can be refilled without the caller queuing again. Left empty
    /// when `repeat` is false, so a one-shot recital holds nothing extra.
    ring: Vec<Step>,
}

/// `None` rather than an empty queue so the static is constructible in a `const` context without
/// depending on which release made `VecDeque::new` const.
static QUEUE: Mutex<Option<Queue>> = Mutex::new(None);

fn with_queue<T>(f: impl FnOnce(&mut Queue) -> T) -> T {
    let mut guard = match QUEUE.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let slot = guard.get_or_insert_with(Queue::default);
    f(slot)
}

/// Queue every place a search will ask about, replacing anything still pending.
///
/// Replacing rather than appending is deliberate: a second search supersedes the first, and a
/// player who re-armed should see the new ring rather than the tail of the old one. The first
/// place is shown on the next drain rather than after a second, so arming is acknowledged at once.
///
/// `repeat` makes the recital a loop rather than a drain: when the last place has been named the
/// ring is refilled and lap two starts at "1 of N" again. It belongs to `Nearby only`, whose
/// search has nowhere to hand over to, and it is the caller's to decide rather than this module's
/// because only the caller knows which row the player pressed.
pub(crate) fn queue_ring(blocks: &[u32], repeat: bool) {
    let total = blocks.len();
    let steps: Vec<Step> = blocks
        .iter()
        .copied()
        .take(MAX_QUEUED)
        .enumerate()
        .map(|(index, block)| Step {
            block,
            ordinal: index + 1,
            total,
        })
        .collect();
    with_queue(|queue| {
        queue.pending = steps.iter().copied().collect();
        queue.ring = if repeat { steps } else { Vec::new() };
        queue.repeat = repeat;
        queue.last_shown = 0;
    });
}

/// Forget whatever is pending, for a search that ended before it was recited.
///
/// This is also how a repeating recital stops, so it clears the lap state with the queue: a
/// cancel that left `repeat` set would refill on the next pump and the banner would outlive the
/// search it describes.
pub(crate) fn clear() {
    with_queue(|queue| {
        queue.pending.clear();
        queue.ring.clear();
        queue.repeat = false;
        queue.last_shown = 0;
    });
}

/// How many places are still waiting to be named.
pub(crate) fn pending() -> usize {
    with_queue(|queue| queue.pending.len())
}

/// The next place to name, or `None` when there is nothing queued or it is not time yet.
///
/// `now_ms` is the caller's clock. A `now_ms` of 0 is treated as "the clock is not readable" and
/// yields nothing, because a zero would otherwise make every call look overdue and recite the
/// whole ring in one frame.
pub(crate) fn pump(now_ms: u64) -> Option<Step> {
    if now_ms == 0 {
        return None;
    }
    with_queue(|queue| {
        if queue.pending.is_empty() {
            // A repeating recital starts its next lap here rather than at the call site, so no
            // caller has to notice the ring ran out. A non-repeating one ends, which is what tells
            // the player of `Both near and far` that the near half is over.
            if !queue.repeat || queue.ring.is_empty() {
                return None;
            }
            queue.pending = queue.ring.iter().copied().collect();
        }
        if queue.last_shown != 0 && now_ms.saturating_sub(queue.last_shown) < STEP_INTERVAL_MS {
            return None;
        }
        queue.last_shown = now_ms;
        queue.pending.pop_front()
    })
}

/// Milliseconds since this module first asked, from a monotonic clock.
///
/// Separate from `lynchpin_use`'s identical helper rather than shared, because the two measure
/// different things and a shared origin would make one module's first call decide the other's
/// zero. Both are elapsed times, so only the differences are ever compared.
fn now_ms() -> u64 {
    use std::sync::OnceLock;
    use std::time::Instant;

    static START: OnceLock<Instant> = OnceLock::new();
    // `max(1)` because zero is this module's spelling of "the clock is not readable", and the
    // very first call would otherwise land on it and be discarded.
    (START.get_or_init(Instant::now).elapsed().as_millis() as u64).max(1)
}

/// Seamless's option-menu object, if any seam has handed it over in this process.
///
/// The one pointer the invade action needs, and the reason the near+far row no longer presses a
/// button: with this in hand `actions::drive_invade_with_owner` is the call the frida prototype
/// made when it landed a real invasion, and without it there is nothing to drive at all.
///
/// Read rather than searched for. `super::OSM` is only ever written by `menu_object::capture_osm`,
/// which validates that `+0x58` leads to something carrying a live session state before storing,
/// so a value here has already been proved to lead somewhere -- unlike the writable-data scan,
/// which on run br-20260916-233426-b38b offered 52516 candidates and picked none.
#[cfg(windows)]
pub(crate) fn captured_menu_object() -> Option<usize> {
    use std::sync::atomic::Ordering;

    let osm = super::OSM.load(Ordering::SeqCst);
    (osm != 0).then_some(osm)
}

/// Host-side stub: there is no Seamless and no menu object.
#[cfg(not(windows))]
pub(crate) fn captured_menu_object() -> Option<usize> {
    None
}

/// The ring a nearby search covers, centred on the block the player is standing in, in the order
/// it is recited and asked about.
///
/// The ring comes from `er_invasion_warp_core::search_ring`, the same arithmetic the query-side
/// ladder walks, so the recital and the search cannot describe different sets of places. A block
/// that is not on the overworld grid yields a ring of one, which is correct rather than degenerate:
/// a legacy dungeon's block and region bytes encode a dungeon and a floor, and stepping them lands
/// somewhere unrelated.
///
/// Separate from queueing it because two things want the same list and they must not compute it
/// twice: the banner recites it, and [`crate::lobby_preflight::arm_sweep`] asks Steam about every
/// entry. A banner naming places the sweep never asked about would be a screen reporting a search
/// that is not the one running.
pub(crate) fn nearby_ring(centre: u32, radius: u8) -> Vec<u32> {
    use er_invasion_warp_core::invasion_warp::BlockKey;

    er_invasion_warp_core::search_ring::ring(BlockKey::from_raw(centre), radius)
        .iter()
        .map(|block| block.raw())
        .collect()
}

/// Name the next place, if one is due. Call once per game tick.
///
/// The announcement goes through `banner::announce_prefilter_step` like every other search line,
/// so it shares the one reject-notice latch and cannot contradict a rejection still on screen.
#[cfg(windows)]
pub(crate) fn pump_and_announce(enabled: bool) {
    let Some(step) = pump(now_ms()) else {
        return;
    };
    super::banner::announce_prefilter_step(enabled, step.block, step.ordinal, step.total);
}

/// Host-side stub: there is no screen to paint.
#[cfg(not(windows))]
pub(crate) fn pump_and_announce(_enabled: bool) {}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every test here drives one process-wide queue, so they have to take turns.
    ///
    /// Emptying it at the start of each test is not enough on its own: cargo runs tests in
    /// parallel threads, and without this lock one test's `clear` lands inside another's
    /// assertions. Measured -- `a_ring_longer_than_the_cap_is_truncated_rather_than_held_whole`
    /// read `pending()` as 0 straight after queueing 74 places, because a neighbour had cleared
    /// between the two lines.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    /// Take the queue for this test and put a known ring in it.
    ///
    /// The returned guard has to be held for the body of the test; dropping it early hands the
    /// queue to whoever is waiting.
    // No `#[must_use]`: `MutexGuard` already carries one, and clippy's `double_must_use` refuses
    // the pair. The doc comment above is what tells a reader to hold the guard.
    fn fresh(blocks: &[u32]) -> std::sync::MutexGuard<'static, ()> {
        let guard = match TEST_LOCK.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        clear();
        queue_ring(blocks, false);
        guard
    }

    #[test]
    fn arming_names_the_first_place_without_waiting_a_second() {
        let _queue = fresh(&[0x3c34_3500, 0x3c33_3500]);
        let first = pump(5_000).expect("the first place is due immediately");
        assert_eq!(first.block, 0x3c34_3500);
        assert_eq!((first.ordinal, first.total), (1, 2));
    }

    #[test]
    fn the_second_place_waits_a_full_interval() {
        let _queue = fresh(&[1, 2]);
        assert!(pump(5_000).is_some());
        assert_eq!(pump(5_000 + STEP_INTERVAL_MS - 1), None, "too early");
        assert_eq!(
            pump(5_000 + STEP_INTERVAL_MS).map(|step| step.block),
            Some(2)
        );
    }

    #[test]
    fn the_places_come_out_in_the_order_they_went_in() {
        let ring: Vec<u32> = (0..5).collect();
        let _queue = fresh(&ring);
        let mut seen = Vec::new();
        let mut now = 1_000;
        while let Some(step) = pump(now) {
            seen.push(step.block);
            now += STEP_INTERVAL_MS;
        }
        assert_eq!(seen, ring);
    }

    #[test]
    fn a_ring_counts_its_own_length_rather_than_the_cap() {
        let _queue = fresh(&[7, 8, 9]);
        let step = pump(1_000).expect("due");
        assert_eq!(step.total, 3);
    }

    #[test]
    fn rearming_replaces_what_was_left_rather_than_queueing_behind_it() {
        let _queue = fresh(&[1, 2, 3]);
        assert!(pump(1_000).is_some());
        queue_ring(&[9], false);
        assert_eq!(pending(), 1);
        assert_eq!(
            pump(1_001).map(|step| step.block),
            Some(9),
            "a re-armed search must not wait out the previous ring's interval"
        );
    }

    #[test]
    fn an_unreadable_clock_names_nothing_rather_than_everything() {
        let _queue = fresh(&[1, 2, 3]);
        assert_eq!(pump(0), None);
        assert_eq!(pending(), 3, "nothing was consumed");
    }

    #[test]
    fn a_ring_longer_than_the_cap_is_truncated_rather_than_held_whole() {
        let long: Vec<u32> = (0..(MAX_QUEUED as u32 + 10)).collect();
        let _queue = fresh(&long);
        assert_eq!(pending(), MAX_QUEUED);
    }

    #[test]
    fn an_empty_ring_is_silent() {
        let _queue = fresh(&[]);
        assert_eq!(pump(1_000), None);
        assert_eq!(pending(), 0);
    }

    #[test]
    fn the_overworld_ring_queued_is_the_one_the_query_side_walks() {
        let _queue = fresh(&[]);
        // Limgrave, the tile run br-20260916-233426-b38b was standing in.
        queue_ring(&nearby_ring(0x3c34_3500, 1), false);
        assert_eq!(pending(), 9, "centre plus its eight neighbours");
        let first = pump(1_000).expect("due");
        assert_eq!(first.block, 0x3c34_3500, "the centre is asked for first");
    }

    #[test]
    fn a_legacy_dungeon_recites_itself_and_nothing_else() {
        let _queue = fresh(&[]);
        // `m10_00_00_00`: the block and region bytes are a dungeon and a floor, not coordinates.
        assert_eq!(nearby_ring(0x0a00_0000, 3).len(), 1);
    }

    #[test]
    fn the_clock_never_hands_back_the_value_that_means_unreadable() {
        assert!(now_ms() >= 1);
    }

    /// The user-visible rule for `Nearby only`: "When I exhausted nearby, the banner didn't come up
    /// again saying it was going through 1-N locations again for a new player. It should keep doing
    /// this on repeat until I cancel."
    #[test]
    fn a_repeating_ring_starts_its_next_lap_at_one_of_n() {
        let _queue = fresh(&[]);
        queue_ring(&[11, 22, 33], true);
        let mut clock = 1_000;
        let mut lap_one = Vec::new();
        for _ in 0..3 {
            let step = pump(clock).expect("the lap is due");
            lap_one.push((step.block, step.ordinal, step.total));
            clock += STEP_INTERVAL_MS;
        }
        assert_eq!(lap_one, vec![(11, 1, 3), (22, 2, 3), (33, 3, 3)]);
        let again = pump(clock).expect("an exhausted repeating ring refills rather than ending");
        assert_eq!(
            (again.block, again.ordinal, again.total),
            (11, 1, 3),
            "lap two starts at the first place, numbered from one again"
        );
    }

    /// `Both near and far` keeps the old behaviour, because for that row the recital ending is the
    /// signal that the near half is over and the far half has begun.
    #[test]
    fn a_one_shot_ring_still_ends_when_it_runs_out() {
        let _queue = fresh(&[]);
        queue_ring(&[11, 22], false);
        let mut clock = 1_000;
        for _ in 0..2 {
            assert!(pump(clock).is_some());
            clock += STEP_INTERVAL_MS;
        }
        assert_eq!(pump(clock), None, "nothing refills a one-shot recital");
    }

    /// Cancelling has to stop the loop as well as empty it: a `clear` that left the repeat flag set
    /// would refill on the next pump and leave the banner describing a search that is over.
    #[test]
    fn clearing_a_repeating_ring_stops_it_repeating() {
        let _queue = fresh(&[]);
        queue_ring(&[11, 22], true);
        assert!(pump(1_000).is_some());
        clear();
        assert_eq!(pump(2_000), None);
        assert_eq!(pending(), 0);
    }
}
