//! The rule-break list in the corner of the screen, and the line each break gets in the text file.
//!
//! The overlay shows only the newest [`SHOWN`] breaks, newest first. Every break goes to the file,
//! with the date and what broke it, so nothing that scrolled off is lost.

use std::collections::VecDeque;
use std::fmt;

use crate::referee::Alert;

/// Breaks on screen at once.
pub const SHOWN: usize = 10;

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
    pub alert: Alert,
    /// The local Steam name when the break was listed.
    pub steam_name: Option<String>,
}

impl Entry {
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

    pub fn push(&mut self, stamp: Stamp, alert: Alert) {
        let steam_name = self.steam_name.clone();
        self.entries.push_front(Entry {
            stamp,
            alert,
            steam_name,
        });
        self.entries.truncate(SHOWN);
    }

    /// Newest first.
    pub fn shown(&self) -> impl Iterator<Item = &Entry> {
        self.entries.iter()
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
        for second in 0..12 {
            feed.push(at(second), alert("Lil Chud", true, ""));
        }
        let seconds: Vec<u8> = feed.shown().map(|e| e.stamp.second).collect();
        assert_eq!(seconds, (2..12).rev().collect::<Vec<u8>>());
    }

    #[test]
    fn the_file_line_carries_the_date_the_player_the_rule_and_the_detail() {
        let entry = Entry {
            stamp: at(5),
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
        feed.push(at(1), alert("Lion Stronk", true, ""));
        feed.set_steam_name(Some("The Nut Wizard".to_owned()));
        feed.push(at(2), alert("Lion Stronk", true, ""));
        let who: Vec<String> = feed.shown().map(Entry::who).collect();
        assert_eq!(who, ["Lion Stronk (The Nut Wizard)", "Lion Stronk (you)"]);
        assert_eq!(
            player_label("Lion Stronk", true, Some("")),
            "Lion Stronk (you)"
        );
    }
}
