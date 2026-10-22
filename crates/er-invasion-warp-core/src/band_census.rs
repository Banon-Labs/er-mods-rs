//! How many hosts this player could invade, per matchmaking band, and how often new ones appear.
//!
//! # What is counted
//!
//! Seamless publishes a `<level band>_<weapon band>` value on every advertisement lobby and its
//! own search compares it for equality, so asking Steam for one band value counts that band
//! directly. The live half adds the filters that make a count mean "invadable by this player":
//! Seamless's advertisement marker, this player's own `lobby_key` (the pool their search can
//! reach) and the open flag every search of Seamless's carries. Nothing here joins or writes a
//! lobby; a count is one `RequestLobbyList` and a walk of the ids it returned.
//!
//! # Steam's cap
//!
//! A lobby list returns at most [`STEAM_RESULT_CAP`] lobbies whatever the count filter asks for,
//! measured 2026-10-21 by asking for 1000 and getting 50 back on the busiest band. A band that hits
//! the cap is asked again as two disjoint halves split on `ykssr_dlc`, and only a half that also
//! hits the cap is reported as a floor ([`Snapshot::saturated`]).
//!
//! # New hosts per minute
//!
//! A count says who is there now; the rate says whether waiting is worth it. [`Arrivals`] compares
//! each finished sweep's lobby ids with the previous sweep's: an id not seen before is a host who
//! opened their world since. A host who rehosts gets a new lobby id and counts again, and one who
//! opens and closes between two sweeps is not seen, so the rate is a floor. The first sweep after a
//! start, a failure or a stall only sets the baseline and counts nothing, so a restart never reads
//! the whole population as new arrivals. The totals persist across sessions in a small text file
//! ([`Arrivals::to_text`]), which is what makes the average a trend rather than one evening.

use std::collections::BTreeSet;

use crate::invade_difficulty::{MAX_LEVEL_BAND, MAX_WEAPON_BAND};

/// Level bands Seamless publishes: 0 to [`MAX_LEVEL_BAND`].
pub const LEVEL_BANDS: usize = 9;
/// Weapon bands Seamless publishes: 0 to [`MAX_WEAPON_BAND`].
pub const WEAPON_BANDS: usize = 4;
/// Every band pair.
pub const BAND_COUNT: usize = 36;

// Written as literals so `scripts/rva_symbols.py` can evaluate them; the compiler keeps them tied
// to the thresholds they count.
const _: () = assert!(LEVEL_BANDS == MAX_LEVEL_BAND as usize + 1);
const _: () = assert!(WEAPON_BANDS == MAX_WEAPON_BAND as usize + 1);
const _: () = assert!(BAND_COUNT == LEVEL_BANDS * WEAPON_BANDS);

/// The most lobbies one lobby list returns, whatever the count filter asks for.
pub const STEAM_RESULT_CAP: usize = 50;
/// What the count filter asks for. Above the cap, so the cap is Steam's and never ours.
pub const RESULT_COUNT_REQUEST: i32 = 1000;

/// The pause between the end of one sweep and the start of the next.
pub const SWEEP_INTERVAL_MS: u64 = 60_000;
/// The pause after a query that did not come back, before the sweep starts over.
pub const RETRY_AFTER_FAILURE_MS: u64 = 15_000;
/// A sweep that took longer than this was paused long enough (a search holds the census off the
/// interface) that its first and last bands describe different moments, so it sets a baseline and
/// counts no arrivals.
pub const MAX_COHERENT_SWEEP_MS: u64 = 5 * 60_000;

/// The index of a band pair, level-major.
#[must_use]
pub const fn band_index(level: usize, weapon: usize) -> usize {
    level * WEAPON_BANDS + weapon
}

/// The `(level, weapon)` pair of a band index.
#[must_use]
pub const fn band_pair(index: usize) -> (usize, usize) {
    (index / WEAPON_BANDS, index % WEAPON_BANDS)
}

/// The value Seamless publishes for a band index, `"<level>_<weapon>"`.
#[must_use]
pub fn band_value(index: usize) -> String {
    let (level, weapon) = band_pair(index);
    format!("{level}_{weapon}")
}

/// Which slice of a band one query asks for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Split {
    /// The whole band.
    Whole,
    /// Only hosts publishing this `ykssr_dlc` value, after the whole band hit the cap.
    Dlc(&'static str),
}

/// The two `ykssr_dlc` values a Seamless host publishes. Disjoint and exhaustive for every host
/// measured (`0` without the expansion, `1` with it), so the halves add up to the band.
pub const DLC_VALUES: [&str; 2] = ["0", "1"];

/// One lobby query the census wants sent.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Query {
    /// The band index.
    pub band: usize,
    pub split: Split,
}

impl Query {
    /// The band value to filter on.
    #[must_use]
    pub fn band_value(&self) -> String {
        band_value(self.band)
    }
}

/// What a sent query came back with.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Answer {
    /// The lobby ids Steam returned.
    Lobbies(Vec<u64>),
    /// The call failed. Never an answer of zero: counting it as one would empty a band that is
    /// full.
    Failed,
}

/// One finished sweep: the hosts in every band.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub counts: [usize; BAND_COUNT],
    /// The band hit Steam's cap even split in two, so its count is a floor.
    pub saturated: [bool; BAND_COUNT],
}

impl Snapshot {
    /// Hosts in every band together.
    #[must_use]
    pub fn total(&self) -> usize {
        self.counts.iter().sum()
    }
}

/// A sweep in progress: one band at a time, each asked whole and split only when it must be.
#[derive(Clone, Debug)]
struct Sweep {
    started_ms: u64,
    band: usize,
    split: Split,
    ids: Vec<BTreeSet<u64>>,
    saturated: [bool; BAND_COUNT],
}

impl Sweep {
    fn new(started_ms: u64) -> Self {
        Self {
            started_ms,
            band: 0,
            split: Split::Whole,
            ids: vec![BTreeSet::new(); BAND_COUNT],
            saturated: [false; BAND_COUNT],
        }
    }

    fn query(&self) -> Query {
        Query {
            band: self.band,
            split: self.split,
        }
    }

    /// Take the answer to [`Sweep::query`]. True when the last band has been answered.
    fn take(&mut self, lobbies: &[u64]) -> bool {
        let at_cap = lobbies.len() >= STEAM_RESULT_CAP;
        match self.split {
            Split::Whole if at_cap => {
                self.split = Split::Dlc(DLC_VALUES[0]);
                return false;
            }
            Split::Whole => self.ids[self.band].extend(lobbies),
            Split::Dlc(value) => {
                self.ids[self.band].extend(lobbies);
                self.saturated[self.band] |= at_cap;
                if value == DLC_VALUES[0] {
                    self.split = Split::Dlc(DLC_VALUES[1]);
                    return false;
                }
            }
        }
        self.split = Split::Whole;
        self.band += 1;
        self.band >= BAND_COUNT
    }
}

/// Hosts that appeared per band, over the minutes the census has watched.
#[derive(Clone, Debug, PartialEq)]
pub struct Arrivals {
    /// New lobby ids seen per band, summed over every pair of consecutive sweeps.
    pub new: [u64; BAND_COUNT],
    /// Minutes between those pairs of sweeps, summed.
    pub minutes: f64,
    /// The previous sweep's ids and when it finished; `None` until a baseline is set.
    previous: Option<(Vec<BTreeSet<u64>>, u64)>,
}

impl Default for Arrivals {
    fn default() -> Self {
        Self {
            new: [0; BAND_COUNT],
            minutes: 0.0,
            previous: None,
        }
    }
}

/// The first line of the persisted file.
const ARRIVALS_HEADER: &str = "# er-invasion-warp band census: new hosts seen per band, and the \
                               minutes watched. Delete this file to start the average over.";

impl Arrivals {
    /// Count a finished sweep against the previous one.
    pub fn observe(&mut self, ids: &[BTreeSet<u64>], now_ms: u64) {
        if let Some((previous, then_ms)) = &self.previous {
            self.minutes += now_ms.saturating_sub(*then_ms) as f64 / 60_000.0;
            for (band, (current, before)) in ids.iter().zip(previous).enumerate() {
                self.new[band] += current.difference(before).count() as u64;
            }
        }
        self.previous = Some((ids.to_vec(), now_ms));
    }

    /// Drop the baseline, so the next sweep sets one instead of being counted against a stale one.
    pub fn forget_baseline(&mut self) {
        self.previous = None;
    }

    /// New hosts per minute in a band, or `None` before any minute has been watched.
    #[must_use]
    pub fn rate(&self, band: usize) -> Option<f64> {
        (self.minutes > 0.0).then(|| self.new[band] as f64 / self.minutes)
    }

    /// The persisted form: the header, the minutes, and one count per band.
    #[must_use]
    pub fn to_text(&self) -> String {
        let new = self
            .new
            .iter()
            .map(u64::to_string)
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{ARRIVALS_HEADER}\nminutes = {:.3}\nnew = {new}\n",
            self.minutes
        )
    }

    /// Read [`Arrivals::to_text`] back. Anything not understood starts the average over rather
    /// than inventing part of one.
    #[must_use]
    pub fn from_text(text: &str) -> Self {
        let mut minutes = None;
        let mut new = None;
        for line in text.lines() {
            let Some((key, value)) = line.split_once('=') else {
                continue;
            };
            match key.trim() {
                "minutes" => {
                    minutes = value
                        .trim()
                        .parse::<f64>()
                        .ok()
                        .filter(|m| m.is_finite() && *m >= 0.0);
                }
                "new" => {
                    let parsed: Option<Vec<u64>> = value
                        .split(',')
                        .map(|count| count.trim().parse::<u64>().ok())
                        .collect();
                    new = parsed.and_then(|counts| <[u64; BAND_COUNT]>::try_from(counts).ok());
                }
                _ => {}
            }
        }
        match (minutes, new) {
            (Some(minutes), Some(new)) => Self {
                new,
                minutes,
                previous: None,
            },
            _ => Self::default(),
        }
    }
}

/// Where the census is between queries.
#[derive(Clone, Debug)]
enum State {
    /// Waiting until this time before the next sweep starts.
    Waiting {
        until_ms: u64,
    },
    Sweeping(Sweep),
}

/// The census: a sweep at a time, the last finished one, and the arrivals.
#[derive(Clone, Debug)]
pub struct Census {
    state: State,
    last: Option<Snapshot>,
    pub arrivals: Arrivals,
    /// Sweeps finished, and queries that failed, this session.
    pub sweeps: u64,
    pub failures: u64,
}

impl Census {
    /// A census that starts its first sweep at once.
    #[must_use]
    pub fn new(arrivals: Arrivals) -> Self {
        Self {
            state: State::Waiting { until_ms: 0 },
            last: None,
            arrivals,
            sweeps: 0,
            failures: 0,
        }
    }

    /// The query to send now, starting a sweep when one is due. `None` while waiting.
    pub fn due(&mut self, now_ms: u64) -> Option<Query> {
        if let State::Waiting { until_ms } = self.state {
            if now_ms < until_ms {
                return None;
            }
            self.state = State::Sweeping(Sweep::new(now_ms));
        }
        match &self.state {
            State::Sweeping(sweep) => Some(sweep.query()),
            State::Waiting { .. } => None,
        }
    }

    /// Take the answer to the query [`Census::due`] last returned.
    pub fn answer(&mut self, now_ms: u64, answer: &Answer) {
        let State::Sweeping(sweep) = &mut self.state else {
            return;
        };
        let lobbies = match answer {
            Answer::Lobbies(lobbies) => lobbies,
            Answer::Failed => {
                self.failures += 1;
                self.arrivals.forget_baseline();
                self.state = State::Waiting {
                    until_ms: now_ms + RETRY_AFTER_FAILURE_MS,
                };
                return;
            }
        };
        if !sweep.take(lobbies) {
            return;
        }
        let sweep = std::mem::replace(
            &mut self.state,
            State::Waiting {
                until_ms: now_ms + SWEEP_INTERVAL_MS,
            },
        );
        let State::Sweeping(sweep) = sweep else {
            return;
        };
        if now_ms.saturating_sub(sweep.started_ms) > MAX_COHERENT_SWEEP_MS {
            self.arrivals.forget_baseline();
        }
        self.arrivals.observe(&sweep.ids, now_ms);
        let mut counts = [0; BAND_COUNT];
        for (count, ids) in counts.iter_mut().zip(&sweep.ids) {
            *count = ids.len();
        }
        self.last = Some(Snapshot {
            counts,
            saturated: sweep.saturated,
        });
        self.sweeps += 1;
    }

    /// The last finished sweep.
    #[must_use]
    pub fn snapshot(&self) -> Option<&Snapshot> {
        self.last.as_ref()
    }

    /// Bands answered so far in the sweep under way, or `None` between sweeps.
    #[must_use]
    pub fn progress(&self) -> Option<usize> {
        match &self.state {
            State::Sweeping(sweep) => Some(sweep.band),
            State::Waiting { .. } => None,
        }
    }
}

/// What the grid shows.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum View {
    /// Hosts there now. The default.
    #[default]
    Hosts,
    /// New hosts per minute, averaged over every minute watched.
    Rate,
}

impl View {
    /// The other view.
    #[must_use]
    pub const fn toggled(self) -> Self {
        match self {
            Self::Hosts => Self::Rate,
            Self::Rate => Self::Hosts,
        }
    }
}

/// One cell of the grid, ready to draw.
#[derive(Clone, Debug, PartialEq)]
pub struct Cell {
    pub text: String,
    /// 0 to 1: how full the cell is against the fullest one, for its shade.
    pub weight: f32,
}

/// A rate as the grid prints it: two decimals, a floor for a rate too small to show.
#[must_use]
pub fn rate_text(rate: f64) -> String {
    if rate > 0.0 && rate < 0.005 {
        "<0.01".to_owned()
    } else {
        format!("{rate:.2}")
    }
}

/// Every cell of the grid in `view`, or `None` when there is nothing to show yet: no sweep has
/// finished for [`View::Hosts`], no minute has been watched for [`View::Rate`].
#[must_use]
pub fn cells(census: &Census, view: View) -> Option<Vec<Cell>> {
    let values: Vec<(f64, String)> = match view {
        View::Hosts => {
            let snapshot = census.snapshot()?;
            (0..BAND_COUNT)
                .map(|band| {
                    let count = snapshot.counts[band];
                    let floor = if snapshot.saturated[band] {
                        "\u{2265}"
                    } else {
                        ""
                    };
                    (count as f64, format!("{floor}{count}"))
                })
                .collect()
        }
        View::Rate => (0..BAND_COUNT)
            .map(|band| {
                let rate = census.arrivals.rate(band)?;
                Some((rate, rate_text(rate)))
            })
            .collect::<Option<_>>()?,
    };
    let peak = values.iter().map(|(value, _)| *value).fold(0.0, f64::max);
    Some(
        values
            .into_iter()
            .map(|(value, text)| Cell {
                text,
                weight: if peak > 0.0 {
                    (value / peak) as f32
                } else {
                    0.0
                },
            })
            .collect(),
    )
}

/// How long the rate has been watched, as the panel says it: `"42 min"`, `"3 h 05 min"`.
#[must_use]
pub fn watched_text(minutes: f64) -> String {
    let whole = minutes.max(0.0).round() as u64;
    if whole < 60 {
        format!("{whole} min")
    } else {
        format!("{} h {:02} min", whole / 60, whole % 60)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ids(range: std::ops::Range<u64>) -> Vec<u64> {
        range.collect()
    }

    /// Answer every query of one sweep with `lobbies(query)`, returning the queries sent.
    fn run_sweep(
        census: &mut Census,
        now_ms: u64,
        lobbies: impl Fn(&Query) -> Vec<u64>,
    ) -> Vec<Query> {
        let mut sent = Vec::new();
        while let Some(query) = census.due(now_ms) {
            sent.push(query);
            census.answer(now_ms, &Answer::Lobbies(lobbies(&query)));
            if census.progress().is_none() {
                break;
            }
        }
        sent
    }

    #[test]
    fn the_bands_are_seamless_values_level_major() {
        assert_eq!(LEVEL_BANDS, 9);
        assert_eq!(WEAPON_BANDS, 4);
        assert_eq!(BAND_COUNT, 36);
        assert_eq!(band_value(0), "0_0");
        assert_eq!(band_value(band_index(2, 1)), "2_1");
        assert_eq!(band_value(BAND_COUNT - 1), "8_3");
        assert_eq!(band_pair(band_index(5, 3)), (5, 3));
    }

    #[test]
    fn a_sweep_asks_every_band_once_and_records_the_counts() {
        let mut census = Census::new(Arrivals::default());
        let sent = run_sweep(&mut census, 0, |query| {
            if query.band == band_index(2, 1) {
                ids(0..7)
            } else {
                Vec::new()
            }
        });
        assert_eq!(sent.len(), BAND_COUNT);
        assert!(sent.iter().all(|query| query.split == Split::Whole));
        let snapshot = census.snapshot().expect("a finished sweep");
        assert_eq!(snapshot.counts[band_index(2, 1)], 7);
        assert_eq!(snapshot.total(), 7);
        assert!(!snapshot.saturated.iter().any(|floor| *floor));
        assert_eq!(census.sweeps, 1);
    }

    #[test]
    fn a_band_at_the_cap_is_asked_again_in_two_dlc_halves() {
        let mut census = Census::new(Arrivals::default());
        let busy = band_index(2, 1);
        let sent = run_sweep(&mut census, 0, |query| {
            match (query.band == busy, query.split) {
                (true, Split::Whole) => ids(0..50),
                (true, Split::Dlc("0")) => ids(0..30),
                (true, Split::Dlc(_)) => ids(100..129),
                _ => Vec::new(),
            }
        });
        assert_eq!(sent.len(), BAND_COUNT + 2);
        let snapshot = census.snapshot().expect("a finished sweep");
        assert_eq!(snapshot.counts[busy], 59);
        assert!(!snapshot.saturated[busy], "neither half hit the cap");

        let mut census = Census::new(Arrivals::default());
        run_sweep(&mut census, 0, |query| {
            match (query.band == busy, query.split) {
                (true, Split::Dlc("0")) => ids(0..50),
                (true, _) => ids(200..250),
                _ => Vec::new(),
            }
        });
        let snapshot = census.snapshot().expect("a finished sweep");
        assert!(snapshot.saturated[busy], "a half at the cap is a floor");
        assert_eq!(snapshot.counts[busy], 100);
    }

    #[test]
    fn the_first_sweep_is_a_baseline_and_the_next_counts_new_ids() {
        let mut census = Census::new(Arrivals::default());
        let band = band_index(4, 3);
        run_sweep(&mut census, 0, |query| {
            if query.band == band {
                vec![1, 2, 3]
            } else {
                Vec::new()
            }
        });
        assert_eq!(census.arrivals.minutes, 0.0);
        assert_eq!(census.arrivals.rate(band), None);
        assert!(cells(&census, View::Rate).is_none());
        assert_eq!(census.due(1), None, "waits out the interval");
        let later = SWEEP_INTERVAL_MS * 2;
        run_sweep(&mut census, later, |query| {
            if query.band == band {
                vec![2, 3, 4, 5]
            } else {
                Vec::new()
            }
        });
        assert_eq!(census.arrivals.new[band], 2);
        assert_eq!(census.arrivals.minutes, 2.0);
        assert_eq!(census.arrivals.rate(band), Some(1.0));
        let rate = cells(&census, View::Rate).expect("a rate");
        assert_eq!(rate[band].text, "1.00");
        assert_eq!(rate[band].weight, 1.0);
        assert_eq!(rate[0].text, "0.00");
    }

    #[test]
    fn a_failure_drops_the_sweep_and_the_baseline() {
        let mut census = Census::new(Arrivals::default());
        run_sweep(&mut census, 0, |_| vec![9]);
        let at = SWEEP_INTERVAL_MS;
        assert!(census.due(at).is_some());
        census.answer(at, &Answer::Failed);
        assert_eq!(census.failures, 1);
        assert_eq!(census.progress(), None);
        assert_eq!(census.due(at + 1), None);
        let retry = at + RETRY_AFTER_FAILURE_MS;
        run_sweep(&mut census, retry, |_| vec![10]);
        // The sweep after the failure is a new baseline, so nothing counts as arrived.
        assert_eq!(census.arrivals.new, [0; BAND_COUNT]);
        assert_eq!(census.arrivals.minutes, 0.0);
        // The snapshot is the newest finished sweep.
        assert_eq!(census.snapshot().map(Snapshot::total), Some(BAND_COUNT));
    }

    #[test]
    fn a_sweep_that_stalled_sets_a_baseline_instead_of_counting() {
        let mut census = Census::new(Arrivals::default());
        run_sweep(&mut census, 0, |_| vec![1]);
        let start = SWEEP_INTERVAL_MS;
        let query = census.due(start).expect("due");
        census.answer(start, &Answer::Lobbies(vec![2]));
        assert_eq!(query.band, 0);
        let late = start + MAX_COHERENT_SWEEP_MS + 1;
        run_sweep(&mut census, late, |_| vec![2]);
        assert_eq!(census.arrivals.minutes, 0.0);
        assert_eq!(census.arrivals.new, [0; BAND_COUNT]);
    }

    #[test]
    fn the_arrivals_survive_a_round_trip_and_garbage_starts_over() {
        let mut arrivals = Arrivals::default();
        arrivals.new[3] = 12;
        arrivals.new[35] = 1;
        arrivals.minutes = 187.25;
        let text = arrivals.to_text();
        assert!(text.starts_with("# er-invasion-warp band census"));
        let back = Arrivals::from_text(&text);
        assert_eq!(back.new, arrivals.new);
        assert!((back.minutes - 187.25).abs() < 1e-9);
        assert_eq!(
            Arrivals::from_text("minutes = 3\nnew = 1,2\n"),
            Arrivals::default()
        );
        assert_eq!(Arrivals::from_text("minutes = -1\n"), Arrivals::default());
        assert_eq!(Arrivals::from_text(""), Arrivals::default());
    }

    #[test]
    fn the_host_cells_mark_a_floor_and_shade_against_the_fullest() {
        let mut census = Census::new(Arrivals::default());
        assert!(cells(&census, View::Hosts).is_none());
        let busy = band_index(0, 0);
        run_sweep(&mut census, 0, |query| match (query.band, query.split) {
            (band, _) if band == busy => ids(0..50),
            (1, _) => ids(0..25),
            _ => Vec::new(),
        });
        let hosts = cells(&census, View::Hosts).expect("a sweep finished");
        assert_eq!(hosts[busy].text, "\u{2265}50");
        assert_eq!(hosts[busy].weight, 1.0);
        assert_eq!(hosts[1].text, "25");
        assert!((hosts[1].weight - 0.5).abs() < 1e-6);
        assert_eq!(hosts[2].weight, 0.0);
    }

    #[test]
    fn the_view_toggles_and_the_words_read_well() {
        assert_eq!(View::default(), View::Hosts);
        assert_eq!(View::Hosts.toggled(), View::Rate);
        assert_eq!(View::Rate.toggled(), View::Hosts);
        assert_eq!(rate_text(0.0), "0.00");
        assert_eq!(rate_text(0.001), "<0.01");
        assert_eq!(rate_text(0.426), "0.43");
        assert_eq!(watched_text(42.4), "42 min");
        assert_eq!(watched_text(185.0), "3 h 05 min");
    }
}
