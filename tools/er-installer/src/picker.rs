//! The picker: a cursor over one screen of rows, and the rules for toggling them.
//!
//! # Why it is a screen and not a printed list
//!
//! The first version printed all 31 rows with their descriptions after every keystroke -- a
//! wall of text the reader had to re-scan each time to find the one line that had changed.
//! This draws a fixed screen instead: one line per mod, the description of the highlighted one
//! only, and a cursor that moves. The information is the same and the reading is not.
//!
//! # A conflict is refused where it happens
//!
//! Ticking a box that cannot coexist with something already ticked is refused at that
//! keystroke, and the message names the mod to untick. Collecting a whole selection and
//! rejecting it at the end makes the user work out which of their choices was the problem,
//! which is the same thing as not telling them.
//!
//! # What is pure and what is not
//!
//! The state machine and [`Picker::render`] are pure functions, so the layout, the scrolling
//! and the refusals are all `cargo test`-able without a terminal. Only [`run`] and
//! [`run_plain`] touch stdin and stdout.

use std::io::{self, BufRead, Write};

use crate::catalog::{CATALOG, CATEGORIES, Conflict, Mod};
use crate::selection;
use crate::tui::{self, Key};

/// What ticking a box did.
#[derive(Debug)]
pub enum Toggle {
    Enabled,
    Disabled,
    /// Refused: ticking this would have produced a selection that cannot load.
    Blocked(Vec<&'static Conflict>),
    /// Refused: a mod already ticked contains this one. Carries that mod's package name.
    Redundant(&'static str),
    OutOfRange,
}

/// A line on screen: either a section title or a mod. Headers are drawn and scrolled through
/// but never land under the cursor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Row {
    Header(&'static str),
    Entry(usize),
}

/// How the loop should continue after a key.
#[derive(Debug, PartialEq, Eq)]
enum Step {
    Continue,
    Install,
    Quit,
}

pub struct Picker {
    /// Parallel to `CATALOG`: whether each mod is ticked.
    ticked: Vec<bool>,
    /// Every line, headers included, in the order they are drawn.
    rows: Vec<Row>,
    /// Index into `rows`. Always an `Entry`.
    cursor: usize,
    /// First row of `rows` visible on screen.
    scroll: usize,
    /// A refusal or a note, shown until the next key.
    message: Option<String>,
    colour: bool,
}

impl Picker {
    pub fn new() -> Self {
        let mut rows = Vec::with_capacity(CATALOG.len() + CATEGORIES.len());
        for (key, title) in CATEGORIES {
            let mut section: Vec<usize> = CATALOG
                .iter()
                .enumerate()
                .filter(|(_, entry)| entry.category == *key)
                .map(|(index, _)| index)
                .collect();
            if section.is_empty() {
                continue;
            }
            section.sort_by_key(|index| CATALOG[*index].label);
            rows.push(Row::Header(title));
            rows.extend(section.into_iter().map(Row::Entry));
        }
        // A category missing from `CATEGORIES` would drop its mods off the screen entirely.
        // The catalog gate forbids that; this keeps the two consistent if it ever slips.
        let placed: Vec<usize> = rows
            .iter()
            .filter_map(|row| match row {
                Row::Entry(index) => Some(*index),
                Row::Header(_) => None,
            })
            .collect();
        let orphans: Vec<usize> = (0..CATALOG.len())
            .filter(|index| !placed.contains(index))
            .collect();
        if !orphans.is_empty() {
            rows.push(Row::Header("Other"));
            rows.extend(orphans.into_iter().map(Row::Entry));
        }

        let cursor = rows
            .iter()
            .position(|row| matches!(row, Row::Entry(_)))
            .unwrap_or(0);
        Self {
            ticked: CATALOG.iter().map(|entry| entry.default_on).collect(),
            rows,
            cursor,
            scroll: 0,
            message: None,
            colour: tui::colour_enabled(),
        }
    }

    /// Render without escape codes. Only the tests need this: a user turns colour off through
    /// `NO_COLOR`, which [`tui::colour_enabled`] already reads.
    #[cfg(test)]
    fn without_colour(mut self) -> Self {
        self.colour = false;
        self
    }

    pub fn chosen(&self) -> Vec<&'static Mod> {
        let chosen: Vec<&'static Mod> = self
            .ticked
            .iter()
            .enumerate()
            .filter(|(_, on)| **on)
            .map(|(index, _)| &CATALOG[index])
            .collect();
        selection::in_display_order(&chosen)
    }

    /// Is this package ticked? By package name, since `included_in` names one.
    fn is_ticked(&self, package: &str) -> bool {
        CATALOG
            .iter()
            .position(|entry| entry.package == package)
            .is_some_and(|index| self.ticked[index])
    }

    fn entry_at(&self, row: usize) -> Option<usize> {
        match self.rows.get(row) {
            Some(Row::Entry(index)) => Some(*index),
            _ => None,
        }
    }

    /// The mod under the cursor.
    fn current(&self) -> Option<&'static Mod> {
        self.entry_at(self.cursor).map(|index| &CATALOG[index])
    }

    /// Move the cursor by whole entries, skipping headers, clamped at both ends.
    fn move_cursor(&mut self, delta: isize) {
        let mut position = self.cursor as isize;
        let mut remaining = delta.abs();
        let step = delta.signum();
        while remaining > 0 {
            let next = position + step;
            if next < 0 || next as usize >= self.rows.len() {
                break;
            }
            position = next;
            if matches!(self.rows[position as usize], Row::Entry(_)) {
                remaining -= 1;
            }
        }
        // A run of headers at the end of a jump would leave the cursor on one; walk back.
        while position >= 0 && !matches!(self.rows[position as usize], Row::Entry(_)) {
            position -= step.clamp(-1, 1);
            if position < 0 || position as usize >= self.rows.len() {
                return;
            }
        }
        if position >= 0 && (position as usize) < self.rows.len() {
            self.cursor = position as usize;
        }
    }

    fn jump_to_edge(&mut self, last: bool) {
        let found = if last {
            self.rows
                .iter()
                .rposition(|row| matches!(row, Row::Entry(_)))
        } else {
            self.rows
                .iter()
                .position(|row| matches!(row, Row::Entry(_)))
        };
        if let Some(position) = found {
            self.cursor = position;
        }
    }

    /// Move to the first entry of the next or previous section.
    fn jump_section(&mut self, forward: bool) {
        let headers: Vec<usize> = self
            .rows
            .iter()
            .enumerate()
            .filter(|(_, row)| matches!(row, Row::Header(_)))
            .map(|(index, _)| index)
            .collect();
        let target = if forward {
            headers
                .iter()
                .find(|header| **header > self.cursor)
                .copied()
        } else {
            let current_header = headers
                .iter()
                .rev()
                .find(|header| **header < self.cursor)
                .copied();
            // Already at the top of a section: go to the one before it, not back to itself.
            match current_header {
                Some(header) if self.cursor > header + 1 => Some(header),
                Some(header) => headers.iter().rev().find(|h| **h < header).copied(),
                None => None,
            }
        };
        if let Some(header) = target
            && header + 1 < self.rows.len()
        {
            self.cursor = header + 1;
        } else if forward {
            self.jump_to_edge(true);
        } else {
            self.jump_to_edge(false);
        }
    }

    /// Tick or untick the row under the cursor. Unticking always works; ticking is refused
    /// when it would pair this mod with something already chosen that it destroys.
    pub fn toggle_current(&mut self) -> Toggle {
        let Some(index) = self.entry_at(self.cursor) else {
            return Toggle::OutOfRange;
        };
        if self.ticked[index] {
            self.ticked[index] = false;
            return Toggle::Disabled;
        }
        let chosen = self.chosen();
        let clashes = selection::conflicts_with(&CATALOG[index], &chosen);
        if !clashes.is_empty() {
            return Toggle::Blocked(clashes);
        }
        // A mod contained in one already ticked. Refused rather than noted, because the guard
        // that would have made it harmless is load-order dependent and this tool decides the
        // order -- see `selection::redundant_with`.
        if let Some(host) = selection::redundant_with(&CATALOG[index], &chosen) {
            return Toggle::Redundant(host);
        }
        self.ticked[index] = true;
        Toggle::Enabled
    }

    pub fn set_defaults(&mut self) {
        self.ticked = CATALOG.iter().map(|entry| entry.default_on).collect();
    }

    pub fn clear(&mut self) {
        self.ticked = vec![false; CATALOG.len()];
    }

    /// Tick everything that can be ticked, in display order, skipping what would conflict with
    /// what is already on. Reports the ones it had to leave off.
    pub fn select_all_compatible(&mut self) -> Vec<&'static Mod> {
        self.clear();
        let mut skipped = Vec::new();
        let order: Vec<usize> = self
            .rows
            .iter()
            .filter_map(|row| match row {
                Row::Entry(index) => Some(*index),
                Row::Header(_) => None,
            })
            .collect();
        for index in order {
            let chosen = self.chosen();
            let entry = &CATALOG[index];
            let blocked = !selection::conflicts_with(entry, &chosen).is_empty()
                || selection::redundant_with(entry, &chosen).is_some();
            if blocked {
                skipped.push(entry);
            } else {
                self.ticked[index] = true;
            }
        }
        skipped
    }

    fn label_for(package: &str) -> &'static str {
        selection::by_package(package).map_or("another mod", |entry| entry.label)
    }

    /// Say which of the two ends of a conflict is the one already chosen.
    pub fn describe(&self, candidate: &Mod, conflict: &Conflict) -> String {
        let other = if conflict.a == candidate.package {
            conflict.b
        } else {
            conflict.a
        };
        format!(
            "{} cannot load with {}: {}.",
            candidate.label,
            Self::label_for(other),
            conflict.explanation
        )
    }

    pub fn summary(&self) -> String {
        let chosen = self.chosen();
        if chosen.is_empty() {
            return "nothing selected -- installs a profile that loads no mods".to_string();
        }
        format!("{} of {} selected", chosen.len(), CATALOG.len())
    }

    /// Scroll so the cursor is on screen, keeping a line of context at each edge where it can.
    fn reframe(&mut self, list_height: usize) {
        if list_height == 0 {
            return;
        }
        if self.cursor < self.scroll {
            self.scroll = self.cursor.saturating_sub(1);
        }
        if self.cursor >= self.scroll + list_height {
            self.scroll = self.cursor + 2 - list_height.min(self.cursor + 2);
        }
        let last_scroll = self.rows.len().saturating_sub(list_height);
        self.scroll = self.scroll.min(last_scroll);
        // A section title directly above the first visible row is worth keeping: a list that
        // opens mid-section gives no clue which one it is.
        if self.scroll > 0
            && matches!(
                self.rows.get(self.scroll.saturating_sub(1)),
                Some(Row::Header(_))
            )
            && self.cursor < self.scroll + list_height - 1
        {
            self.scroll -= 1;
        }
    }

    /// How many lines the list gets, given the whole screen.
    fn list_height(height: usize) -> usize {
        // Two for the header, five for the detail pane, one for the key hints.
        height.saturating_sub(8).max(3)
    }

    /// Draw the whole screen. Pure: same state and size, same string.
    pub fn render(&mut self, width: usize, height: usize) -> String {
        let width = width.clamp(40, 200);
        let list_height = Self::list_height(height);
        self.reframe(list_height);

        let mut out = String::new();
        let selected = self.chosen().len();
        out.push_str(&self.title_bar(width, selected));
        out.push('\n');

        let mut drawn = 0;
        for position in self.scroll..self.rows.len() {
            if drawn == list_height {
                break;
            }
            out.push_str(&self.row_line(position, width));
            out.push('\n');
            drawn += 1;
        }
        for _ in drawn..list_height {
            out.push('\n');
        }

        out.push_str(&self.detail_pane(width));
        out.push_str(&self.hint_line(width));
        out
    }

    fn title_bar(&self, width: usize, selected: usize) -> String {
        let left = "ELDEN RING MODS";
        let right = if selected == 0 {
            "nothing selected".to_string()
        } else {
            format!("{selected} of {} selected", CATALOG.len())
        };
        let gap = width.saturating_sub(left.len() + right.len() + 2);
        let bar = format!(" {left}{}{right} ", " ".repeat(gap));
        let rule = "-".repeat(width);
        if self.colour {
            format!("\x1b[1m{bar}\x1b[0m\n\x1b[2m{rule}\x1b[0m")
        } else {
            format!("{bar}\n{rule}")
        }
    }

    fn row_line(&self, position: usize, width: usize) -> String {
        match self.rows[position] {
            Row::Header(title) => {
                let text = format!("  {}", title.to_uppercase());
                if self.colour {
                    format!("\x1b[1;36m{}\x1b[0m", truncate(&text, width))
                } else {
                    truncate(&text, width)
                }
            }
            Row::Entry(index) => {
                let entry = &CATALOG[index];
                let here = position == self.cursor;
                let arrow = if here { ">" } else { " " };
                let tick = if self.ticked[index] { "x" } else { " " };
                let mut notes: Vec<&str> = Vec::new();
                if entry.audience == "diagnostic" {
                    notes.push("dev tool");
                }
                if entry.needs_seamless {
                    notes.push("needs Seamless");
                }
                if entry.opt_in_only {
                    notes.push("changes things");
                }
                // Only worth saying while the bigger mod is actually ticked. Shown always, it
                // would read as a warning against a row that is perfectly good on its own.
                let included_note;
                if let Some(host) = entry
                    .included_in
                    .iter()
                    .copied()
                    .find(|host| self.is_ticked(host))
                {
                    included_note = format!("already in {}", Self::label_for(host));
                    notes.push(&included_note);
                }
                let note = if notes.is_empty() {
                    String::new()
                } else {
                    format!("  ({})", notes.join("; "))
                };
                let text = truncate(&format!("{arrow} [{tick}] {}{note}", entry.label), width);
                if !self.colour {
                    return text;
                }
                let padded = format!("{text}{}", " ".repeat(width.saturating_sub(text.len())));
                if here {
                    format!("\x1b[7m{padded}\x1b[0m")
                } else if self.ticked[index] {
                    format!("\x1b[32m{text}\x1b[0m")
                } else {
                    text
                }
            }
        }
    }

    fn detail_pane(&self, width: usize) -> String {
        let rule = "-".repeat(width);
        let mut out = if self.colour {
            format!("\x1b[2m{rule}\x1b[0m\n")
        } else {
            format!("{rule}\n")
        };

        if let Some(message) = &self.message {
            for line in wrap(message, width.saturating_sub(2)).into_iter().take(3) {
                out.push_str(&if self.colour {
                    format!("\x1b[33m {line}\x1b[0m\n")
                } else {
                    format!(" {line}\n")
                });
            }
            for _ in wrap(message, width.saturating_sub(2)).len().min(3)..3 {
                out.push('\n');
            }
            return out;
        }

        let Some(entry) = self.current() else {
            out.push_str("\n\n\n");
            return out;
        };
        out.push_str(&if self.colour {
            format!("\x1b[1m {}\x1b[0m\n", truncate(entry.label, width - 1))
        } else {
            format!(" {}\n", truncate(entry.label, width - 1))
        });

        let mut body = wrap(entry.blurb, width.saturating_sub(2));
        if let Some(config) = entry.config {
            body.push(format!("Settings: {config} in the game folder."));
        }
        let mut written = 0;
        for line in body.into_iter().take(2) {
            out.push_str(&format!(" {line}\n"));
            written += 1;
        }
        for _ in written..2 {
            out.push('\n');
        }
        out
    }

    /// The key hints, in the order they may be dropped from the right as the terminal narrows.
    ///
    /// `q quit` sits early on purpose. The first version listed the hints in the order they
    /// felt natural and put quit last, so at 80 columns -- the width a terminal opens at --
    /// the line truncated and the one key a stuck user needs was the one not shown.
    const HINTS: &'static [&'static str] = &[
        "up/down move",
        "space toggle",
        "enter install",
        "q quit",
        "d recommended",
        "tab section",
        "n none",
        "s all",
    ];

    /// The hint that must survive any width: someone who cannot work out how to leave is stuck
    /// in an alternate screen with their terminal in raw mode.
    const ESCAPE_HINT: &'static str = "q quit";

    fn hint_line(&self, width: usize) -> String {
        let mut text = String::from(" ");
        for hint in Self::HINTS {
            let addition = if text.len() > 1 {
                format!("   {hint}")
            } else {
                (*hint).to_string()
            };
            // Skip rather than stop: a hint too long for what is left must not take the
            // shorter ones after it down with it, and `q quit` is one of those.
            if text.chars().count() + addition.chars().count() > width {
                continue;
            }
            text.push_str(&addition);
        }
        if !text.contains(Self::ESCAPE_HINT) {
            text = truncate(&format!(" {}", Self::ESCAPE_HINT), width);
        }
        if self.colour {
            format!("\x1b[2m{text}\x1b[0m")
        } else {
            text
        }
    }

    /// Apply one key. Pure, so the whole interaction is testable without a terminal.
    fn handle(&mut self, key: Key, list_height: usize) -> Step {
        self.message = None;
        match key {
            Key::Up | Key::Char('k') => self.move_cursor(-1),
            Key::Down | Key::Char('j') => self.move_cursor(1),
            Key::PageUp => self.move_cursor(-(list_height as isize / 2).max(1)),
            Key::PageDown => self.move_cursor((list_height as isize / 2).max(1)),
            Key::Home => self.jump_to_edge(false),
            Key::End => self.jump_to_edge(true),
            Key::Right | Key::Char('\t') => self.jump_section(true),
            Key::Left => self.jump_section(false),
            Key::Space => {
                let candidate = self.current();
                match (self.toggle_current(), candidate) {
                    (Toggle::Blocked(clashes), Some(candidate)) => {
                        let mut lines: Vec<String> = clashes
                            .iter()
                            .map(|conflict| self.describe(candidate, conflict))
                            .collect();
                        lines.push("Untick the other one first.".to_string());
                        self.message = Some(lines.join(" "));
                    }
                    (Toggle::Redundant(host), Some(candidate)) => {
                        self.message = Some(format!(
                            "{} already does this -- {} is part of it. Loading both puts two \
                             copies of one feature in the game, and which of them ends up \
                             running depends on load order. Untick {} if you want this one on \
                             its own.",
                            Self::label_for(host),
                            candidate.label,
                            Self::label_for(host)
                        ));
                    }
                    _ => {}
                }
            }
            Key::Enter => return Step::Install,
            Key::Char('d') => self.set_defaults(),
            Key::Char('n') => self.clear(),
            Key::Char('s') => {
                let skipped = self.select_all_compatible();
                if !skipped.is_empty() {
                    self.message = Some(format!(
                        "Left off, each conflicting with something already on: {}",
                        skipped
                            .iter()
                            .map(|entry| entry.label)
                            .collect::<Vec<_>>()
                            .join(", ")
                    ));
                }
            }
            Key::Char('q') | Key::Escape | Key::Interrupt => return Step::Quit,
            Key::Char(_) => {}
        }
        Step::Continue
    }
}

impl Default for Picker {
    fn default() -> Self {
        Self::new()
    }
}

/// Cut a string to `width` display columns, marking the cut so a clipped label does not read
/// as a shorter one.
fn truncate(text: &str, width: usize) -> String {
    if text.chars().count() <= width {
        return text.to_string();
    }
    let keep = width.saturating_sub(1);
    text.chars().take(keep).collect::<String>() + "~"
}

/// Break text at word boundaries. A word longer than the line is left over-long rather than
/// split, because the only words that long here are file names and cutting one is worse.
fn wrap(text: &str, width: usize) -> Vec<String> {
    if width == 0 {
        return vec![text.to_string()];
    }
    let mut lines = Vec::new();
    let mut current = String::new();
    for word in text.split_whitespace() {
        if current.is_empty() {
            current.push_str(word);
        } else if current.chars().count() + 1 + word.chars().count() <= width {
            current.push(' ');
            current.push_str(word);
        } else {
            lines.push(std::mem::take(&mut current));
            current.push_str(word);
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    if lines.is_empty() {
        lines.push(String::new());
    }
    lines
}

/// Drive the picker on a terminal. Falls back to the line-based prompt when raw mode is not
/// available, which is what makes this runnable from a pipe and from a test.
pub fn run(picker: &mut Picker) -> io::Result<Option<Vec<&'static Mod>>> {
    let Some(_raw) = tui::RawMode::enter() else {
        // Say which picker this is. Falling back silently leaves someone who has seen the
        // full-screen one thinking it broke, when the cause is that this run has no terminal
        // to drive -- input is piped, or the terminal refused raw mode.
        println!(
            "\n(Numbered list: this run has no interactive terminal, so the full-screen picker \
             cannot start. Run it directly in a terminal for the arrow-key version, or pass \
             --plain to choose this one.)"
        );
        return run_plain(picker);
    };
    let mut stdin = io::stdin();
    loop {
        let (width, height) = tui::size();
        let frame = picker.render(width, height);
        tui::paint(&frame)?;

        let Some(key) = tui::decode(&mut stdin) else {
            return Ok(None);
        };
        match picker.handle(key, Picker::list_height(height)) {
            Step::Continue => {}
            Step::Install => return Ok(Some(picker.chosen())),
            Step::Quit => return Ok(None),
        }
    }
}

const PLAIN_HELP: &str = "\
  Type row numbers to tick or untick them (for example: 1 4 7)
  a  accept and install          d  reset to the recommended set
  s  select everything possible  n  select nothing
  q  quit without installing";

/// The line-based picker, for a terminal that will not go into raw mode and for piped input.
pub fn run_plain(picker: &mut Picker) -> io::Result<Option<Vec<&'static Mod>>> {
    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();
    loop {
        println!("{}", picker.plain_list());
        println!("{}\n{PLAIN_HELP}", picker.summary());
        print!("\n> ");
        io::stdout().flush()?;

        let Some(line) = lines.next() else {
            println!();
            return Ok(None);
        };
        let line = line?;
        let trimmed = line.trim().to_ascii_lowercase();
        match trimmed.as_str() {
            "a" | "accept" | "install" => return Ok(Some(picker.chosen())),
            "q" | "quit" | "exit" => return Ok(None),
            "d" | "defaults" => picker.set_defaults(),
            "n" | "none" => picker.clear(),
            "s" | "all" => {
                let skipped = picker.select_all_compatible();
                if !skipped.is_empty() {
                    println!(
                        "\nLeft off, each conflicting with something already on: {}",
                        skipped
                            .iter()
                            .map(|entry| entry.label)
                            .collect::<Vec<_>>()
                            .join(", ")
                    );
                }
            }
            _ => {
                let numbers: Vec<usize> = trimmed
                    .split(|c: char| c.is_whitespace() || c == ',')
                    .filter_map(|token| token.parse::<usize>().ok())
                    .collect();
                if numbers.is_empty() {
                    println!("\nDid not understand {trimmed:?}.\n{PLAIN_HELP}");
                }
                for number in numbers {
                    println!("{}", picker.toggle_numbered(number));
                }
            }
        }
    }
}

impl Picker {
    /// Numbered rows for the line-based picker, without the per-row descriptions that made the
    /// printed form unreadable.
    fn plain_list(&self) -> String {
        let mut out = String::new();
        let mut number = 0;
        for row in &self.rows {
            match row {
                Row::Header(title) => out.push_str(&format!("\n  {}\n", title.to_uppercase())),
                Row::Entry(index) => {
                    number += 1;
                    let entry = &CATALOG[*index];
                    let tick = if self.ticked[*index] { "x" } else { " " };
                    out.push_str(&format!("  {number:>3} [{tick}] {}\n", entry.label));
                }
            }
        }
        out
    }

    /// Toggle by the number the line-based picker printed, returning what to say about it.
    fn toggle_numbered(&mut self, number: usize) -> String {
        let entries: Vec<usize> = self
            .rows
            .iter()
            .enumerate()
            .filter(|(_, row)| matches!(row, Row::Entry(_)))
            .map(|(position, _)| position)
            .collect();
        let Some(position) = number.checked_sub(1).and_then(|n| entries.get(n)).copied() else {
            return format!("\nThere is no row {number}.");
        };
        let saved = self.cursor;
        self.cursor = position;
        let candidate = self.current();
        let outcome = self.toggle_current();
        self.cursor = saved;
        match (outcome, candidate) {
            (Toggle::Blocked(clashes), Some(candidate)) => {
                let mut text = String::from("\n");
                for conflict in clashes {
                    text.push_str(&format!("  {}\n", self.describe(candidate, conflict)));
                }
                text.push_str("  Untick the other one first if this is the one you want.");
                text
            }
            _ => String::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const WIDTH: usize = 90;
    const HEIGHT: usize = 24;

    fn plain_picker() -> Picker {
        Picker::new().without_colour()
    }

    fn cursor_onto(picker: &mut Picker, package: &str) {
        let index = CATALOG
            .iter()
            .position(|entry| entry.package == package)
            .expect("package is in the catalog");
        picker.cursor = picker
            .rows
            .iter()
            .position(|row| *row == Row::Entry(index))
            .expect("row is on screen");
    }

    #[test]
    fn a_new_picker_starts_on_the_default_set() {
        let picker = plain_picker();
        let chosen: Vec<_> = picker.chosen().iter().map(|entry| entry.package).collect();
        let expected: Vec<_> = selection::default_selection()
            .iter()
            .map(|entry| entry.package)
            .collect();
        assert_eq!(chosen.len(), expected.len());
        for package in expected {
            assert!(
                chosen.contains(&package),
                "{package} missing from a fresh picker"
            );
        }
    }

    #[test]
    fn every_catalog_row_appears_exactly_once() {
        let picker = plain_picker();
        let mut entries: Vec<usize> = picker
            .rows
            .iter()
            .filter_map(|row| match row {
                Row::Entry(index) => Some(*index),
                Row::Header(_) => None,
            })
            .collect();
        assert_eq!(entries.len(), CATALOG.len());
        entries.sort_unstable();
        entries.dedup();
        assert_eq!(
            entries.len(),
            CATALOG.len(),
            "a mod is listed twice or not at all"
        );
    }

    #[test]
    fn the_cursor_starts_on_a_mod_not_a_section_title() {
        let picker = plain_picker();
        assert!(matches!(picker.rows[picker.cursor], Row::Entry(_)));
    }

    #[test]
    fn the_cursor_never_lands_on_a_section_title() {
        let mut picker = plain_picker();
        for _ in 0..CATALOG.len() * 2 {
            picker.handle(Key::Down, 10);
            assert!(
                matches!(picker.rows[picker.cursor], Row::Entry(_)),
                "cursor landed on a header going down"
            );
        }
        for _ in 0..CATALOG.len() * 2 {
            picker.handle(Key::Up, 10);
            assert!(
                matches!(picker.rows[picker.cursor], Row::Entry(_)),
                "cursor landed on a header going up"
            );
        }
    }

    #[test]
    fn the_cursor_stops_at_both_ends_rather_than_wrapping() {
        let mut picker = plain_picker();
        for _ in 0..200 {
            picker.handle(Key::Down, 10);
        }
        let last = picker.cursor;
        picker.handle(Key::Down, 10);
        assert_eq!(picker.cursor, last, "the cursor ran off the bottom");

        for _ in 0..200 {
            picker.handle(Key::Up, 10);
        }
        let first = picker.cursor;
        picker.handle(Key::Up, 10);
        assert_eq!(picker.cursor, first, "the cursor ran off the top");
    }

    #[test]
    fn home_and_end_reach_the_first_and_last_mod() {
        let mut picker = plain_picker();
        picker.handle(Key::End, 10);
        let last = picker.cursor;
        picker.handle(Key::Home, 10);
        assert!(picker.cursor < last);
        assert!(matches!(picker.rows[last], Row::Entry(_)));
        assert!(matches!(picker.rows[picker.cursor], Row::Entry(_)));
    }

    #[test]
    fn tab_moves_between_sections() {
        let mut picker = plain_picker();
        let first = picker.current().unwrap().category;
        picker.handle(Key::Right, 10);
        assert_ne!(picker.current().unwrap().category, first, "tab stayed put");
        picker.handle(Key::Left, 10);
        assert_eq!(
            picker.current().unwrap().category,
            first,
            "back-tab did not return"
        );
    }

    #[test]
    fn space_toggles_the_row_under_the_cursor() {
        let mut picker = plain_picker();
        picker.clear();
        cursor_onto(&mut picker, "er-quickload");
        picker.handle(Key::Space, 10);
        assert_eq!(picker.chosen().len(), 1);
        picker.handle(Key::Space, 10);
        assert!(picker.chosen().is_empty());
    }

    #[test]
    fn ticking_a_conflicting_row_is_refused_and_says_which_to_untick() {
        let mut picker = plain_picker();
        picker.clear();
        cursor_onto(&mut picker, "er-quickload");
        picker.handle(Key::Space, 10);
        cursor_onto(&mut picker, "er-loading-portrait");
        picker.handle(Key::Space, 10);

        let chosen: Vec<_> = picker.chosen().iter().map(|e| e.package).collect();
        assert_eq!(
            chosen,
            vec!["er-quickload"],
            "a refused tick changed the set"
        );
        let message = picker.message.clone().expect("a refusal should say so");
        let product = selection::by_package("er-quickload").unwrap();
        assert!(message.contains(product.label), "message was: {message}");
        assert!(
            !message.contains("er-quickload"),
            "package name leaked: {message}"
        );
    }

    #[test]
    fn ticking_something_already_included_is_refused_and_names_the_host() {
        let included = CATALOG
            .iter()
            .find(|entry| !entry.included_in.is_empty())
            .expect("the catalog records at least one included-in relation");
        let host = included.included_in[0];

        let mut picker = plain_picker();
        picker.clear();
        cursor_onto(&mut picker, host);
        picker.handle(Key::Space, 10);
        cursor_onto(&mut picker, included.package);
        picker.handle(Key::Space, 10);

        let chosen: Vec<_> = picker.chosen().iter().map(|e| e.package).collect();
        assert_eq!(
            chosen,
            vec![host],
            "the contained mod was ticked beside the one that already has it"
        );
        let message = picker.message.clone().expect("a refusal should say so");
        assert!(
            message.contains("already does this"),
            "message was: {message}"
        );
        assert!(
            message.contains(Picker::label_for(host)),
            "message was: {message}"
        );
    }

    #[test]
    fn the_contained_mod_is_still_tickable_on_its_own() {
        let included = CATALOG
            .iter()
            .find(|entry| !entry.included_in.is_empty())
            .expect("the catalog records at least one included-in relation");
        let mut picker = plain_picker();
        picker.clear();
        cursor_onto(&mut picker, included.package);
        assert!(matches!(picker.toggle_current(), Toggle::Enabled));
        assert_eq!(picker.chosen().len(), 1);
    }

    #[test]
    fn select_all_never_installs_one_feature_twice() {
        let mut picker = plain_picker();
        picker.select_all_compatible();
        let chosen = picker.chosen();
        assert!(
            selection::redundancies_within(&chosen).is_empty(),
            "select-all produced a set carrying one feature twice"
        );
    }

    #[test]
    fn a_row_is_only_marked_included_while_its_host_is_ticked() {
        let included = CATALOG
            .iter()
            .find(|entry| !entry.included_in.is_empty())
            .expect("the catalog records at least one included-in relation");
        let host = included.included_in[0];

        let mut picker = plain_picker();
        picker.clear();
        let alone = picker.render(WIDTH, 60);
        let row_alone = alone
            .lines()
            .find(|line| line.contains(included.label))
            .expect("the row is drawn");
        assert!(
            !row_alone.contains("already in"),
            "a mod good on its own was marked redundant: {row_alone}"
        );

        cursor_onto(&mut picker, host);
        picker.handle(Key::Space, 10);
        let together = picker.render(WIDTH, 60);
        let row_together = together
            .lines()
            .find(|line| line.contains(included.label))
            .expect("the row is drawn");
        assert!(
            row_together.contains("already in"),
            "the overlap was not shown: {row_together}"
        );
    }

    #[test]
    fn an_included_pair_is_not_also_a_conflict() {
        // The two answers are mutually exclusive: safe-and-redundant, or unsafe. The catalog
        // gate checks the tables; this checks the code that reads them agrees.
        for entry in CATALOG {
            for host in entry.included_in {
                let host_mod = selection::by_package(host).expect("included_in names a real mod");
                assert!(
                    selection::conflicts_within(&[entry, host_mod]).is_empty(),
                    "{} is both included in and conflicting with {host}",
                    entry.package
                );
            }
        }
    }

    #[test]
    fn the_message_clears_on_the_next_key() {
        let mut picker = plain_picker();
        picker.clear();
        cursor_onto(&mut picker, "er-quickload");
        picker.handle(Key::Space, 10);
        cursor_onto(&mut picker, "er-loading-portrait");
        picker.handle(Key::Space, 10);
        assert!(picker.message.is_some());
        picker.handle(Key::Down, 10);
        assert!(
            picker.message.is_none(),
            "the refusal outlived its keystroke"
        );
    }

    #[test]
    fn enter_installs_and_q_quits() {
        let mut picker = plain_picker();
        assert_eq!(picker.handle(Key::Enter, 10), Step::Install);
        assert_eq!(picker.handle(Key::Char('q'), 10), Step::Quit);
        assert_eq!(picker.handle(Key::Escape, 10), Step::Quit);
        assert_eq!(picker.handle(Key::Interrupt, 10), Step::Quit);
    }

    #[test]
    fn select_all_produces_a_loadable_set() {
        let mut picker = plain_picker();
        picker.handle(Key::Char('s'), 10);
        let chosen = picker.chosen();
        assert!(
            selection::conflicts_within(&chosen).is_empty(),
            "select-all produced a conflicting set"
        );
        assert!(picker.message.is_some(), "it should say what it left off");
        assert_eq!(
            chosen.len() + picker.select_all_compatible().len(),
            CATALOG.len()
        );
    }

    #[test]
    fn a_frame_is_exactly_as_tall_as_the_terminal() {
        let mut picker = plain_picker();
        for height in [10usize, 24, 40, 60] {
            let frame = picker.render(WIDTH, height);
            let lines = frame.lines().count();
            let expected = 2 + Picker::list_height(height) + 4 + 1;
            assert_eq!(
                lines, expected,
                "at height {height} the frame was {lines} lines"
            );
        }
    }

    #[test]
    fn no_line_is_wider_than_the_terminal() {
        let mut picker = plain_picker();
        for width in [40usize, 60, 80, 120] {
            let frame = picker.render(width, HEIGHT);
            for line in frame.lines() {
                assert!(
                    line.chars().count() <= width,
                    "a line ran to {} at width {width}: {line:?}",
                    line.chars().count()
                );
            }
        }
    }

    #[test]
    fn the_cursor_row_is_always_on_screen() {
        let mut picker = plain_picker();
        let list_height = Picker::list_height(HEIGHT);
        for _ in 0..CATALOG.len() + 4 {
            picker.render(WIDTH, HEIGHT);
            assert!(
                picker.cursor >= picker.scroll && picker.cursor < picker.scroll + list_height,
                "cursor {} is outside the window at scroll {}",
                picker.cursor,
                picker.scroll
            );
            picker.handle(Key::Down, list_height);
        }
    }

    #[test]
    fn only_the_highlighted_mods_description_is_shown() {
        let mut picker = plain_picker();
        cursor_onto(&mut picker, "er-quickload");
        let frame = picker.render(WIDTH, 40);
        let product = selection::by_package("er-quickload").unwrap();
        let first_words: String = product
            .blurb
            .split_whitespace()
            .take(4)
            .collect::<Vec<_>>()
            .join(" ");
        assert!(
            frame.contains(&first_words),
            "the cursor row's description is missing"
        );

        let others = CATALOG
            .iter()
            .filter(|entry| entry.package != "er-quickload")
            .filter(|entry| {
                let words: String = entry
                    .blurb
                    .split_whitespace()
                    .take(5)
                    .collect::<Vec<_>>()
                    .join(" ");
                frame.contains(&words)
            })
            .count();
        assert_eq!(others, 0, "{others} other descriptions were drawn as well");
    }

    #[test]
    fn a_short_terminal_still_renders_a_usable_list() {
        let mut picker = plain_picker();
        let frame = picker.render(40, 8);
        assert!(frame.lines().count() >= 8);
        assert!(frame.contains('['), "no rows were drawn at all");
    }

    #[test]
    fn the_hint_line_names_every_key_when_there_is_room() {
        let picker = plain_picker();
        let hints = picker.hint_line(200);
        for hint in Picker::HINTS {
            assert!(hints.contains(hint), "{hint} is not in the hints: {hints}");
        }
    }

    #[test]
    fn a_narrow_terminal_keeps_the_quit_hint_and_never_overflows() {
        let picker = plain_picker();
        // 80 is the width a terminal opens at, and is where the first version dropped `q quit`.
        // 12 is narrower than anything `render` will pass, and still must not strand anyone.
        for width in [12usize, 20, 40, 60, 72, 80, 100] {
            let hints = picker.hint_line(width);
            assert!(
                hints.chars().count() <= width,
                "hints ran to {} at width {width}",
                hints.chars().count()
            );
            assert!(
                hints.contains("q quit"),
                "no way out shown at width {width}: {hints}"
            );
        }
    }

    #[test]
    fn colour_is_only_emitted_when_it_is_asked_for() {
        let mut plain = Picker::new().without_colour();
        assert!(!plain.render(WIDTH, HEIGHT).contains('\x1b'));

        let mut coloured = Picker::new();
        coloured.colour = true;
        assert!(coloured.render(WIDTH, HEIGHT).contains('\x1b'));
    }

    #[test]
    fn truncate_marks_the_cut() {
        assert_eq!(truncate("short", 10), "short");
        assert_eq!(truncate("abcdefghij", 5), "abcd~");
        assert_eq!(truncate("abc", 3), "abc");
    }

    #[test]
    fn wrap_breaks_at_words_and_never_loses_one() {
        let text = "the quick brown fox jumps over the lazy dog";
        let lines = wrap(text, 12);
        for line in &lines {
            assert!(line.chars().count() <= 12, "{line:?} is too long");
        }
        assert_eq!(lines.join(" "), text, "wrapping changed the text");
    }

    #[test]
    fn wrap_leaves_an_over_long_word_whole() {
        let lines = wrap("a verylongunbreakableword b", 8);
        assert!(lines.iter().any(|line| line == "verylongunbreakableword"));
    }

    #[test]
    fn the_plain_list_has_one_line_per_mod_and_no_descriptions() {
        let picker = plain_picker();
        let listed = picker.plain_list();
        for entry in CATALOG {
            assert!(
                listed.contains(entry.label),
                "{} is not listed",
                entry.package
            );
            assert!(
                !listed.contains(entry.blurb),
                "{} printed its description in the compact list",
                entry.package
            );
        }
    }

    #[test]
    fn the_plain_list_refuses_a_conflicting_number_and_names_the_row() {
        let mut picker = plain_picker();
        picker.clear();
        let product_row = picker
            .rows
            .iter()
            .filter(|row| matches!(row, Row::Entry(_)))
            .position(|row| {
                matches!(row, Row::Entry(index)
                    if CATALOG[*index].package == "er-quickload")
            })
            .expect("the product is listed")
            + 1;
        let portrait_row = picker
            .rows
            .iter()
            .filter(|row| matches!(row, Row::Entry(_)))
            .position(|row| {
                matches!(row, Row::Entry(index)
                    if CATALOG[*index].package == "er-loading-portrait")
            })
            .expect("the portrait is listed")
            + 1;

        assert_eq!(picker.toggle_numbered(product_row), "");
        let refusal = picker.toggle_numbered(portrait_row);
        assert!(refusal.contains("cannot load with"), "got: {refusal:?}");
        assert_eq!(picker.chosen().len(), 1);
        assert!(picker.toggle_numbered(999).contains("no row 999"));
    }
}
