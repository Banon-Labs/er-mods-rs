//! The rule-break list in the corner of the screen, and the line each break gets in the text file.
//!
//! The overlay shows only the newest [`SHOWN`] breaks, newest first, and each row fades out on its
//! own clock: solid for [`ROW_HOLD`], gone at [`ROW_LIFETIME`] ([`row_alpha`]). The age is measured
//! from the monotonic [`Instant`] the row was listed at, never from its wall-clock [`Stamp`]. A row
//! that has faded stays in the feed; it is only not drawn. Every break goes to the file, with the
//! date and what broke it, so nothing that faded or scrolled off is lost.

use std::collections::VecDeque;
use std::fmt;
use std::time::{Duration, Instant};

use crate::referee::Alert;

/// Breaks on screen at once.
pub const SHOWN: usize = 10;

/// How long a row stays at full opacity after it is listed: long enough to read the longest rule
/// with its detail.
pub const ROW_HOLD: Duration = Duration::from_secs(6);

/// How long a row is on screen in all. It fades from [`ROW_HOLD`] to here and is not drawn after.
pub const ROW_LIFETIME: Duration = Duration::from_secs(10);

/// A row's opacity `age` after it was listed: 1 up to [`ROW_HOLD`], easing to 0 at
/// [`ROW_LIFETIME`], and 0 from then on. The ease is a smoothstep, so the fade starts and ends
/// without a jump in speed.
#[must_use]
pub fn row_alpha(age: Duration) -> f32 {
    if age <= ROW_HOLD {
        return 1.0;
    }
    if age >= ROW_LIFETIME {
        return 0.0;
    }
    let t = (age - ROW_HOLD).as_secs_f32() / (ROW_LIFETIME - ROW_HOLD).as_secs_f32();
    let eased = t * t * (3.0 - 2.0 * t);
    (1.0 - eased).clamp(0.0, 1.0)
}

/// Local wall-clock time of a break.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Stamp {
    pub year: u16,
    pub month: u8,
    pub day: u8,
    pub hour: u8,
    pub minute: u8,
    pub second: u8,
}

impl Stamp {
    /// `HH:MM:SS`, the overlay's column.
    pub fn time(&self) -> String {
        format!("{:02}:{:02}:{:02}", self.hour, self.minute, self.second)
    }
}

impl fmt::Display for Stamp {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{:04}-{:02}-{:02} {}",
            self.year,
            self.month,
            self.day,
            self.time()
        )
    }
}

/// A player as listed: the character's name, and for whoever runs this copy their Steam name
/// beside it, so a file read later says which account broke the rule. "(you)" stands in only
/// when Steam could not be asked.
pub fn player_label(player: &str, is_local: bool, steam_name: Option<&str>) -> String {
    match (is_local, steam_name) {
        (false, _) => player.to_owned(),
        (true, Some(steam)) if !steam.is_empty() => format!("{player} ({steam})"),
        (true, _) => format!("{player} (you)"),
    }
}

/// One break as listed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub stamp: Stamp,
    /// When the break was listed, on the monotonic clock its fade is measured by.
    pub listed: Instant,
    pub alert: Alert,
    /// The local Steam name when the break was listed.
    pub steam_name: Option<String>,
}

impl Entry {
    /// The row's opacity at `now` ([`row_alpha`]).
    #[must_use]
    pub fn alpha_at(&self, now: Instant) -> f32 {
        row_alpha(now.saturating_duration_since(self.listed))
    }

    /// The player as listed ([`player_label`]).
    pub fn who(&self) -> String {
        player_label(
            &self.alert.player,
            self.alert.is_local,
            self.steam_name.as_deref(),
        )
    }

    /// The rule as listed: the ruleset's words with the numbers in force, or a ban's own name.
    pub fn rule(&self) -> &str {
        &self.alert.text
    }

    /// The line in the text file: date and time, player, rule, and what broke it.
    pub fn file_line(&self) -> String {
        let mut line = format!("{}  {}  {}", self.stamp, self.who(), self.rule());
        if !self.alert.detail.is_empty() {
            line.push_str("  --  ");
            line.push_str(&self.alert.detail);
        }
        line
    }
}

/// The newest [`SHOWN`] breaks.
#[derive(Debug, Default)]
pub struct Feed {
    entries: VecDeque<Entry>,
    steam_name: Option<String>,
}

impl Feed {
    pub const fn new() -> Self {
        Self {
            entries: VecDeque::new(),
            steam_name: None,
        }
    }

    /// The local Steam name every later entry is listed with.
    pub fn set_steam_name(&mut self, name: Option<String>) {
        self.steam_name = name;
    }

    pub fn steam_name(&self) -> Option<&str> {
        self.steam_name.as_deref()
    }

    /// List a break at `stamp` on the wall clock and `listed` on the monotonic one.
    pub fn push(&mut self, stamp: Stamp, listed: Instant, alert: Alert) {
        let steam_name = self.steam_name.clone();
        self.entries.push_front(Entry {
            stamp,
            listed,
            alert,
            steam_name,
        });
        self.entries.truncate(SHOWN);
    }

    /// Every row the feed holds, faded or not, newest first.
    pub fn shown(&self) -> impl Iterator<Item = &Entry> {
        self.entries.iter()
    }

    /// The rows still on screen at `now`, newest first, each with its opacity. A row that has
    /// faded out is left out here and kept in the feed.
    pub fn visible(&self, now: Instant) -> impl Iterator<Item = (&Entry, f32)> {
        self.entries.iter().filter_map(move |entry| {
            let alpha = entry.alpha_at(now);
            (alpha > 0.0).then_some((entry, alpha))
        })
    }

    /// The list's own opacity at `now`, for its title and background: its most opaque row's. The
    /// panel stays solid while any row is, fades out with the last one, and is 0 -- nothing to
    /// draw -- once every row has faded or the feed is empty.
    #[must_use]
    pub fn panel_alpha(&self, now: Instant) -> f32 {
        self.entries
            .iter()
            .map(|entry| entry.alpha_at(now))
            .fold(0.0, f32::max)
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rules::Rule;

    fn alert(player: &str, is_local: bool, detail: &str) -> Alert {
        Alert {
            key: 1,
            player: player.to_owned(),
            is_local,
            rule: Rule::SpiritAshes,
            text: Rule::SpiritAshes.text().to_owned(),
            detail: detail.to_owned(),
        }
    }

    fn at(second: u8) -> Stamp {
        Stamp {
            year: 2026,
            month: 10,
            day: 16,
            hour: 16,
            minute: 12,
            second,
        }
    }

    #[test]
    fn the_overlay_keeps_the_newest_ten_newest_first() {
        let mut feed = Feed::default();
        let base = Instant::now();
        for second in 0..12 {
            let listed = base + Duration::from_secs(u64::from(second));
            feed.push(at(second), listed, alert("Lil Chud", true, ""));
        }
        let seconds: Vec<u8> = feed.shown().map(|e| e.stamp.second).collect();
        assert_eq!(seconds, (2..12).rev().collect::<Vec<u8>>());
    }

    #[test]
    fn a_row_is_solid_through_its_hold_then_eases_out_by_its_lifetime() {
        let ms = Duration::from_millis;
        assert_eq!(row_alpha(Duration::ZERO), 1.0);
        assert_eq!(row_alpha(ROW_HOLD - ms(1)), 1.0);
        assert_eq!(row_alpha(ROW_HOLD), 1.0);
        // Just past the hold the fade has begun, barely.
        let started = row_alpha(ROW_HOLD + ms(1));
        assert!(started < 1.0 && started > 0.999, "{started}");
        // Halfway through the fade a smoothstep is at exactly half.
        let middle = ROW_HOLD + (ROW_LIFETIME - ROW_HOLD) / 2;
        assert!((row_alpha(middle) - 0.5).abs() < 1e-6);
        // Just before the end it is all but gone, and at the end it is gone.
        let ending = row_alpha(ROW_LIFETIME - ms(1));
        assert!(ending > 0.0 && ending < 0.001, "{ending}");
        assert_eq!(row_alpha(ROW_LIFETIME), 0.0);
        assert_eq!(row_alpha(ROW_LIFETIME + ms(1)), 0.0);
        // A row left up for a year is still gone; the arithmetic does not wrap.
        assert_eq!(row_alpha(ROW_LIFETIME * 3_153_600), 0.0);
    }

    #[test]
    fn a_row_never_brightens_as_it_ages() {
        let mut last = 1.0;
        for step in 0..=1200_u64 {
            let alpha = row_alpha(Duration::from_millis(step * 10));
            assert!((0.0..=1.0).contains(&alpha), "{alpha} at {step}0 ms");
            assert!(alpha <= last, "{alpha} after {last} at {step}0 ms");
            last = alpha;
        }
        assert_eq!(last, 0.0);
    }

    #[test]
    fn the_fade_follows_the_monotonic_clock_not_the_wall_clock_label() {
        let earlier = Instant::now();
        let listed = earlier + Duration::from_secs(1);
        // The wall-clock stamp says nothing about age: two rows stamped two minutes apart but
        // listed at the same instant fade together.
        let mut feed = Feed::default();
        let mut early = at(0);
        early.minute = 11;
        feed.push(early, listed, alert("Early", false, ""));
        feed.push(at(59), listed, alert("Late", false, ""));
        let now = listed + ROW_HOLD + (ROW_LIFETIME - ROW_HOLD) / 2;
        let alphas: Vec<f32> = feed.visible(now).map(|(_, a)| a).collect();
        assert_eq!(alphas.len(), 2);
        assert_eq!(alphas[0], alphas[1]);
        // A clock read before the row was listed is age zero, not a panic.
        let before = feed.shown().next().map(|e| e.alpha_at(earlier));
        assert_eq!(before, Some(1.0));
    }

    #[test]
    fn faded_rows_leave_the_screen_but_stay_in_the_feed() {
        let base = Instant::now();
        let secs = Duration::from_secs;
        let mut feed = Feed::default();
        feed.push(at(0), base, alert("Oldest", false, ""));
        feed.push(at(3), base + secs(3), alert("Middle", false, ""));
        feed.push(at(8), base + secs(8), alert("Newest", false, "Storm Stomp"));

        // At 12 s the oldest (12 s old) is gone, the middle one (9 s) is fading, the newest (4 s)
        // is solid, and the panel is as solid as its newest row.
        let now = base + secs(12);
        let on_screen: Vec<(&str, f32)> = feed
            .visible(now)
            .map(|(e, a)| (e.alert.player.as_str(), a))
            .collect();
        assert_eq!(on_screen.len(), 2);
        assert_eq!(on_screen[0], ("Newest", 1.0));
        assert_eq!(on_screen[1].0, "Middle");
        assert_eq!(on_screen[1].1, row_alpha(secs(9)));
        assert!(on_screen[1].1 > 0.0 && on_screen[1].1 < 1.0);
        assert_eq!(feed.panel_alpha(now), 1.0);
        // The feed itself, which the file line is taken from, still holds all three.
        assert_eq!(feed.shown().count(), 3);

        // With only the newest left and fading, the panel fades with it.
        let now = base + secs(16);
        assert_eq!(feed.visible(now).count(), 1);
        assert_eq!(feed.panel_alpha(now), row_alpha(secs(8)));
        assert!(feed.panel_alpha(now) < 1.0);

        // Once every row has faded there is nothing to draw, title and background included.
        let now = base + secs(8) + ROW_LIFETIME;
        assert_eq!(feed.visible(now).count(), 0);
        assert_eq!(feed.panel_alpha(now), 0.0);
        assert_eq!(feed.shown().count(), 3);
        assert!(!feed.is_empty());

        // A new break brings the panel back.
        feed.push(at(20), now, alert("Again", false, ""));
        assert_eq!(feed.panel_alpha(now), 1.0);
        assert_eq!(feed.visible(now).count(), 1);
    }

    #[test]
    fn an_empty_feed_draws_nothing() {
        let feed = Feed::new();
        assert_eq!(feed.panel_alpha(Instant::now()), 0.0);
        assert_eq!(feed.visible(Instant::now()).count(), 0);
    }

    #[test]
    fn the_file_line_carries_the_date_the_player_the_rule_and_the_detail() {
        let entry = Entry {
            stamp: at(5),
            listed: Instant::now(),
            alert: alert("Lil Chud", true, "Noble Sorcerer Ashes"),
            steam_name: Some("The Nut Wizard".to_owned()),
        };
        assert_eq!(entry.stamp.time(), "16:12:05");
        assert_eq!(
            entry.file_line(),
            "2026-10-16 16:12:05  Lil Chud (The Nut Wizard)  Spirit Ashes  --  Noble Sorcerer Ashes"
        );
        let remote = Entry {
            stamp: at(5),
            listed: Instant::now(),
            alert: alert("Opponent", false, ""),
            steam_name: Some("The Nut Wizard".to_owned()),
        };
        assert_eq!(
            remote.file_line(),
            "2026-10-16 16:12:05  Opponent  Spirit Ashes"
        );
    }

    #[test]
    fn the_local_player_is_named_by_steam_and_marked_you_only_without_it() {
        let mut feed = Feed::default();
        let listed = Instant::now();
        feed.push(at(1), listed, alert("Lion Stronk", true, ""));
        feed.set_steam_name(Some("The Nut Wizard".to_owned()));
        feed.push(at(2), listed, alert("Lion Stronk", true, ""));
        let who: Vec<String> = feed.shown().map(Entry::who).collect();
        assert_eq!(who, ["Lion Stronk (The Nut Wizard)", "Lion Stronk (you)"]);
        assert_eq!(
            player_label("Lion Stronk", true, Some("")),
            "Lion Stronk (you)"
        );
    }
}
