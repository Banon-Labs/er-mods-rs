//! The run of world markers laid along a route, and the rule for when it is a NEW run.
//!
//! Ungated so `cargo test` compiles it: the pile-up this exists to prevent was a logic bug, not a
//! game-memory one, and a windows-gated module is never built by the host test run.

use crate::geometry;

/// A trail of world markers, laid a few at a time.
///
/// # Why it is laid incrementally
///
/// The first version spawned a whole route's worth of markers in one pass and repeated that every
/// couple of seconds. Two things went wrong, both visible in the first live run: the markers piled
/// up, because each pass laid a fresh set on top of the last, and the whole trail appeared at once
/// for a route the player had already started walking away from.
///
/// A trail is now laid a few markers per pass, from your feet outwards, and it STOPS the moment
/// the route changes -- the far end of a route to somebody who is moving was never going to be
/// where they are by the time you got there.
#[derive(Default)]
pub(crate) struct Trail {
    /// Every marker position for the current route, in order from the player outwards.
    spots: Vec<[f32; 3]>,
    /// How many of them have been spawned. Laying stops when this reaches `spots.len()`.
    laid: usize,
}

impl Trail {
    /// Point this trail at a new route, if it is actually a different one.
    ///
    /// Returns whether the trail was restarted. An unchanged route must NOT restart: the markers
    /// for it are already in the world, and re-laying them is precisely what made them accumulate.
    pub(crate) fn retarget(&mut self, spots: Vec<[f32; 3]>) -> bool {
        if same_route(&self.spots, &spots) {
            return false;
        }
        self.spots = spots;
        self.laid = 0;
        true
    }

    /// The next few positions to spawn, advancing the cursor past them.
    pub(crate) fn next_batch(&mut self, batch: usize) -> &[[f32; 3]] {
        let from = self.laid;
        let to = (from + batch).min(self.spots.len());
        self.laid = to;
        &self.spots[from..to]
    }

    pub(crate) fn finished(&self) -> bool {
        self.laid >= self.spots.len()
    }
}

/// Are these two marker runs the same trail?
///
/// Compared position by position rather than by route identity, because the navmesh returns a
/// fresh answer every refresh and two answers to the same question differ in the last decimal
/// place without differing anywhere a player could see. Treating those as a new trail is what
/// re-laid markers on top of themselves.
fn same_route(previous: &[[f32; 3]], next: &[[f32; 3]]) -> bool {
    /// How far a marker may move before the trail counts as a different one. Below the spacing,
    /// well above navmesh jitter.
    const MOVED_METERS: f32 = 1.0;
    previous.len() == next.len()
        && previous
            .iter()
            .zip(next)
            .all(|(a, b)| geometry::length(geometry::sub(*a, *b)) <= MOVED_METERS)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn route(count: usize) -> Vec<[f32; 3]> {
        (0..count).map(|i| [i as f32 * 4.0, 0.0, 0.0]).collect()
    }

    /// The bug from the first live run: every pass laid a fresh set of markers over the last, so
    /// they accumulated until the trail was a wall.
    #[test]
    fn an_unchanged_route_does_not_lay_a_second_trail_over_the_first() {
        let mut trail = Trail::default();
        assert!(trail.retarget(route(6)));
        while !trail.finished() {
            trail.next_batch(3);
        }
        assert!(
            !trail.retarget(route(6)),
            "an unchanged route must not restart"
        );
        assert!(trail.finished(), "and must not lay another marker");
    }

    /// Navmesh answers to the same question differ in the last decimal without differing anywhere
    /// a player could see. Those must not count as a new trail either.
    #[test]
    fn navmesh_jitter_is_not_a_new_route() {
        let mut trail = Trail::default();
        trail.retarget(route(4));
        let jittered: Vec<[f32; 3]> = route(4)
            .into_iter()
            .map(|p| [p[0] + 0.01, p[1] - 0.02, p[2] + 0.005])
            .collect();
        assert!(!trail.retarget(jittered));
    }

    /// A route that genuinely moved restarts, and restarting STOPS the old laying immediately --
    /// which is the point: the far end of a route to somebody who is moving is already wrong.
    #[test]
    fn a_moved_route_restarts_and_abandons_the_rest_of_the_old_one() {
        let mut trail = Trail::default();
        trail.retarget(route(10));
        trail.next_batch(3);
        assert_eq!(trail.laid, 3);
        let moved: Vec<[f32; 3]> = route(10)
            .into_iter()
            .map(|p| [p[0], p[1], p[2] + 25.0])
            .collect();
        assert!(trail.retarget(moved));
        assert_eq!(trail.laid, 0, "laying restarts from the player's feet");
    }

    /// A different NUMBER of markers is a different route even if the shared prefix matches.
    #[test]
    fn a_shorter_or_longer_route_is_a_new_route() {
        let mut trail = Trail::default();
        trail.retarget(route(8));
        assert!(trail.retarget(route(5)));
    }

    #[test]
    fn markers_are_laid_a_few_at_a_time_from_the_players_feet_outwards() {
        let mut trail = Trail::default();
        trail.retarget(route(7));
        assert_eq!(trail.next_batch(3).len(), 3);
        assert_eq!(trail.next_batch(3).len(), 3);
        // The tail is short, and asking for more than remains must not panic or over-read.
        assert_eq!(trail.next_batch(3).len(), 1);
        assert!(trail.finished());
        assert_eq!(trail.next_batch(3).len(), 0);
    }

    #[test]
    fn an_empty_route_is_finished_immediately() {
        let mut trail = Trail::default();
        assert!(trail.finished());
        assert_eq!(trail.next_batch(4).len(), 0);
    }
}
