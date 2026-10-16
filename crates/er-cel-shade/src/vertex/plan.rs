//! The vertex builder's bookkeeping (`docs/plans/er-shader-editor-vertex.md`, section 7.5): which
//! material groups to build first, how many pipelines a present may create, how the patch
//! requests are cut into batches, and the counts the telemetry reports. Pure, so all of it is
//! tested on the host.
//!
//! # Laziness
//!
//! "Everything" is 2277 vertex shaders and 8429 pipelines in run1, most of them never on screen.
//! A group is built only once it has been bound since the layers were enabled; groups bound in
//! the last [`RECENT_MS`] go first, most binds first, then the rest in the order they were first
//! bound. A group stays vanilla until every one of its pipelines has its variant, and then all of
//! them switch in one table swap.
//!
//! # Budget
//!
//! A pipeline's creation is a synchronous translation and driver compile under vkd3d-proton
//! (design section 8), so the builder creates at most [`CREATE_BUDGET_MS`] of them, and at most
//! [`CREATES_PER_PRESENT`], per present, and waits for the next present for more.

use std::collections::BTreeMap;
use std::ops::Range;

/// A group bound within this many milliseconds is "seen in recent frames".
pub const RECENT_MS: u64 = 10_000;

/// Milliseconds of pipeline creation one present allows.
pub const CREATE_BUDGET_MS: f32 = 6.0;

/// Pipelines one present allows, however fast they were made.
pub const CREATES_PER_PRESENT: u32 = 24;

/// Game vertex shaders one patch request carries at most, and their bytes: the request and the
/// answer each go in one 4 MiB frame, and a shader patches in about 3.4 ms on one core, so a batch
/// is under a quarter of a second of the helper's time.
pub const BATCH_ITEMS: usize = 48;
pub const BATCH_BYTES: usize = 1 << 20;

/// What the builder knows of one group's binds.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Activity {
    /// The sum of the group's entries' bind counters when last read.
    pub seen: u64,
    /// Binds in the last reading interval.
    pub delta: u64,
    /// When a reading last found new binds, and when the first did.
    pub last_bound_ms: Option<u64>,
    pub first_bound_ms: Option<u64>,
}

impl Activity {
    /// Take a new reading of the group's counters, `total`, at `now_ms`. A total below the last
    /// one -- an entry removed with its counter -- starts the count again from it.
    pub fn read(&mut self, total: u64, now_ms: u64) {
        let delta = total.saturating_sub(self.seen);
        self.seen = total;
        self.delta = delta;
        if delta > 0 {
            self.last_bound_ms = Some(now_ms);
            self.first_bound_ms.get_or_insert(now_ms);
        }
    }

    /// Bound in the last [`RECENT_MS`]?
    #[must_use]
    pub fn recent(&self, now_ms: u64) -> bool {
        self.last_bound_ms
            .is_some_and(|at| now_ms.saturating_sub(at) <= RECENT_MS)
    }
}

/// The order to build groups in: those bound in the last [`RECENT_MS`], most binds in the last
/// reading first; then every other group bound at least once, earliest first bind first. Groups
/// never bound are left out. Ties keep `groups`' order.
#[must_use]
pub fn build_order<K: Copy>(groups: &[(K, Activity)], now_ms: u64) -> Vec<K> {
    let mut recent: Vec<&(K, Activity)> = groups
        .iter()
        .filter(|(_, activity)| activity.recent(now_ms))
        .collect();
    recent.sort_by_key(|(_, activity)| core::cmp::Reverse(activity.delta));
    let mut older: Vec<&(K, Activity)> = groups
        .iter()
        .filter(|(_, activity)| !activity.recent(now_ms) && activity.first_bound_ms.is_some())
        .collect();
    older.sort_by_key(|(_, activity)| activity.first_bound_ms);
    recent
        .into_iter()
        .chain(older)
        .map(|(key, _)| *key)
        .collect()
}

/// How much pipeline creation the current present has left.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Budget {
    present: u64,
    spent_ms: f32,
    made: u32,
}

impl Budget {
    /// May one more pipeline be created during present `present`? A new present starts a new
    /// budget.
    pub fn allows(&mut self, present: u64) -> bool {
        if present != self.present {
            *self = Self {
                present,
                spent_ms: 0.0,
                made: 0,
            };
        }
        self.spent_ms < CREATE_BUDGET_MS && self.made < CREATES_PER_PRESENT
    }

    /// One pipeline took `ms` during present `present`.
    pub fn spend(&mut self, present: u64, ms: f32) {
        if present != self.present {
            self.allows(present);
        }
        self.spent_ms += ms.max(0.0);
        self.made += 1;
    }
}

/// `sizes` cut into consecutive batches of at most [`BATCH_ITEMS`] and [`BATCH_BYTES`]; a shader
/// larger than the byte limit goes alone.
#[must_use]
pub fn batches(sizes: &[usize]) -> Vec<Range<usize>> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut bytes = 0;
    for (at, size) in sizes.iter().enumerate() {
        let full = at - start >= BATCH_ITEMS || (at > start && bytes + size > BATCH_BYTES);
        if full {
            out.push(start..at);
            start = at;
            bytes = 0;
        }
        bytes += size;
    }
    if start < sizes.len() {
        out.push(start..sizes.len());
    }
    out
}

/// Where one group's build stands, for the telemetry and the panel.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Phase {
    /// Bound, waiting for its turn.
    Pending,
    /// Its vertex shaders are spliced and checked; waiting to be created.
    Spliced,
    /// `made` of `total` variants created.
    Creating { made: usize, total: usize },
    /// Every pipeline of the group binds its variant.
    Live,
    /// The group stays vanilla: why.
    Refused(String),
}

impl Phase {
    /// The word the telemetry counts by.
    #[must_use]
    pub const fn word(&self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Spliced => "spliced",
            Self::Creating { .. } => "created",
            Self::Live => "live",
            Self::Refused(_) => "refused",
        }
    }
}

/// How many groups stand in each phase, by [`Phase::word`].
#[must_use]
pub fn phase_counts<'a>(
    phases: impl IntoIterator<Item = &'a Phase>,
) -> BTreeMap<&'static str, usize> {
    let mut counts: BTreeMap<&'static str, usize> =
        ["pending", "spliced", "created", "live", "refused"]
            .into_iter()
            .map(|word| (word, 0))
            .collect();
    for phase in phases {
        *counts.entry(phase.word()).or_default() += 1;
    }
    counts
}

/// The value at percentile `p` (0 to 100) of `values`, nearest rank.
#[must_use]
pub fn percentile(values: &[f32], p: f32) -> Option<f32> {
    crate::editor::worker::percentile(values, p)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn activity(delta: u64, last: Option<u64>, first: Option<u64>) -> Activity {
        Activity {
            seen: 0,
            delta,
            last_bound_ms: last,
            first_bound_ms: first,
        }
    }

    #[test]
    fn recent_groups_go_first_by_their_binds_then_the_rest_by_first_bind() {
        let now = 100_000;
        let groups = [
            ("never", activity(0, None, None)),
            ("old", activity(0, Some(50_000), Some(20_000))),
            ("busy", activity(900, Some(now), Some(90_000))),
            ("older", activity(0, Some(40_000), Some(10_000))),
            ("quiet", activity(3, Some(now - 5_000), Some(95_000))),
        ];
        assert_eq!(
            build_order(&groups, now),
            vec!["busy", "quiet", "older", "old"]
        );
    }

    #[test]
    fn a_reading_finds_new_binds_and_starts_over_after_a_drop() {
        let mut seen = Activity::default();
        seen.read(0, 1_000);
        assert_eq!((seen.delta, seen.last_bound_ms), (0, None));
        seen.read(40, 2_000);
        assert_eq!(
            (seen.delta, seen.last_bound_ms, seen.first_bound_ms),
            (40, Some(2_000), Some(2_000))
        );
        seen.read(45, 3_000);
        assert_eq!((seen.delta, seen.first_bound_ms), (5, Some(2_000)));
        seen.read(10, 4_000);
        assert_eq!(seen.delta, 0);
        assert!(seen.recent(3_000 + RECENT_MS));
        assert!(!seen.recent(3_001 + RECENT_MS));
    }

    #[test]
    fn a_present_creates_within_its_time_and_count() {
        let mut budget = Budget::default();
        assert!(budget.allows(10));
        budget.spend(10, 4.0);
        assert!(budget.allows(10));
        budget.spend(10, 2.5);
        assert!(!budget.allows(10));
        // The next present starts again.
        assert!(budget.allows(11));
        for _ in 0..CREATES_PER_PRESENT {
            assert!(budget.allows(11));
            budget.spend(11, 0.01);
        }
        assert!(!budget.allows(11));
        assert!(budget.allows(12));
    }

    #[test]
    fn batches_hold_to_both_limits() {
        assert!(batches(&[]).is_empty());
        let small = vec![10_000; 100];
        assert_eq!(batches(&small), vec![0..48, 48..96, 96..100]);
        let big = vec![600_000, 600_000, 300_000, 2_000_000, 1];
        assert_eq!(batches(&big), vec![0..1, 1..3, 3..4, 4..5]);
    }

    #[test]
    fn phases_count_every_word_even_when_none_stand_there() {
        let phases = [
            Phase::Live,
            Phase::Pending,
            Phase::Creating { made: 3, total: 9 },
            Phase::Live,
            Phase::Refused("no POSITION0".to_owned()),
        ];
        let counts = phase_counts(&phases);
        assert_eq!(counts["live"], 2);
        assert_eq!(counts["pending"], 1);
        assert_eq!(counts["created"], 1);
        assert_eq!(counts["refused"], 1);
        assert_eq!(counts["spliced"], 0);
        assert_eq!(counts.len(), 5);
    }
}
