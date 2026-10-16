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

/// One break as listed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub stamp: Stamp,
    pub alert: Alert,
}

impl Entry {
    /// The player as listed: their name, marked when it is whoever runs this copy.
    pub fn who(&self) -> String {
        if self.alert.is_local {
            format!("{} (you)", self.alert.player)
        } else {
            self.alert.player.clone()
        }
    }

    /// The rule's own words.
    pub fn rule(&self) -> &'static str {
        self.alert.rule.text()
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
}

impl Feed {
    pub const fn new() -> Self {
        Self {
            entries: VecDeque::new(),
        }
    }

    pub fn push(&mut self, stamp: Stamp, alert: Alert) {
        self.entries.push_front(Entry { stamp, alert });
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
        };
        assert_eq!(entry.stamp.time(), "16:12:05");
        assert_eq!(
            entry.file_line(),
            "2026-10-16 16:12:05  Lil Chud (you)  Spirit Ashes  --  Noble Sorcerer Ashes"
        );
        let remote = Entry {
            stamp: at(5),
            alert: alert("Opponent", false, ""),
        };
        assert_eq!(
            remote.file_line(),
            "2026-10-16 16:12:05  Opponent  Spirit Ashes"
        );
    }
}
