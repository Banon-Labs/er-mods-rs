//! The console picker: a numbered list of ticked boxes, and the rules for toggling them.
//!
//! A conflict is refused at the moment of ticking rather than at the end, and the refusal names
//! what to untick. Collecting a whole selection and then rejecting it makes the user work out
//! which of their choices was the problem, which is the same thing as not telling them.
//!
//! The state machine and the rendering are pure functions over a `Picker`, so the behaviour is
//! `cargo test`-able without a terminal. Only [`run`] touches stdin and stdout.

use std::io::{self, BufRead, Write};

use crate::catalog::{CATALOG, CATEGORIES, Conflict, Mod};
use crate::selection;

/// What ticking a box did.
#[derive(Debug)]
pub enum Toggle {
    Enabled,
    Disabled,
    /// Refused: ticking this would have produced a selection that cannot load.
    Blocked(Vec<&'static Conflict>),
    OutOfRange,
}

/// One line of input, parsed.
#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    Toggle(Vec<usize>),
    Accept,
    Defaults,
    None,
    All,
    Details,
    Quit,
    /// Anything that parsed as nothing useful, carried back so the prompt can say why.
    Unrecognised(String),
}

pub struct Picker {
    /// Parallel to `CATALOG`: whether each mod is ticked.
    ticked: Vec<bool>,
    /// The order rows are shown and numbered in -- indices into `CATALOG`.
    order: Vec<usize>,
    /// Whether rows show their blurb.
    pub details: bool,
}

impl Picker {
    /// Start from the catalog's default ticks, grouped by category in the display order.
    pub fn new() -> Self {
        let mut order: Vec<usize> = Vec::with_capacity(CATALOG.len());
        for (key, _) in CATEGORIES {
            let mut section: Vec<usize> = CATALOG
                .iter()
                .enumerate()
                .filter(|(_, entry)| entry.category == *key)
                .map(|(index, _)| index)
                .collect();
            section.sort_by_key(|index| CATALOG[*index].label);
            order.extend(section);
        }
        // A category missing from `CATEGORIES` would drop its rows off the list entirely; the
        // catalog gate forbids that, and this keeps the two consistent if it ever slips.
        for index in 0..CATALOG.len() {
            if !order.contains(&index) {
                order.push(index);
            }
        }
        Self {
            ticked: CATALOG.iter().map(|entry| entry.default_on).collect(),
            order,
            details: true,
        }
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

    fn catalog_index(&self, row: usize) -> Option<usize> {
        row.checked_sub(1)
            .and_then(|zero| self.order.get(zero))
            .copied()
    }

    /// Tick or untick one row. Unticking is always allowed; ticking is refused when it would
    /// pair the row with something already chosen that it destroys.
    pub fn toggle(&mut self, row: usize) -> Toggle {
        let Some(index) = self.catalog_index(row) else {
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
        self.ticked[index] = true;
        Toggle::Enabled
    }

    pub fn set_defaults(&mut self) {
        self.ticked = CATALOG.iter().map(|entry| entry.default_on).collect();
    }

    pub fn clear(&mut self) {
        self.ticked = vec![false; CATALOG.len()];
    }

    /// Tick everything that can be ticked, taking rows in display order and skipping any that
    /// would conflict with what is already on. Reports the ones it had to skip.
    pub fn select_all_compatible(&mut self) -> Vec<&'static Mod> {
        self.clear();
        let mut skipped = Vec::new();
        for row in 1..=CATALOG.len() {
            let index = self.catalog_index(row).expect("row is within the catalog");
            if let Toggle::Blocked(_) = self.toggle(row) {
                skipped.push(&CATALOG[index]);
            }
        }
        skipped
    }

    /// The label the installer uses when naming a conflicting package back to the user. A
    /// package name in a message a player reads is the internal spelling leaking out.
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
            "{} cannot be loaded with {}: {}.",
            candidate.label,
            Self::label_for(other),
            conflict.explanation
        )
    }

    pub fn render(&self) -> String {
        let mut out = String::new();
        let mut shown_category: Option<&str> = None;
        for (position, index) in self.order.iter().enumerate() {
            let entry = &CATALOG[*index];
            if shown_category != Some(entry.category) {
                let title = CATEGORIES
                    .iter()
                    .find(|(key, _)| *key == entry.category)
                    .map_or(entry.category, |(_, title)| *title);
                out.push_str(&format!("\n  {}\n", title.to_uppercase()));
                shown_category = Some(entry.category);
            }
            let box_ = if self.ticked[*index] { "x" } else { " " };
            out.push_str(&format!("  {:>3} [{}] {}", position + 1, box_, entry.label));
            let mut notes: Vec<&str> = Vec::new();
            if entry.audience == "diagnostic" {
                notes.push("development tool");
            }
            if entry.needs_seamless {
                notes.push("needs Seamless Co-op");
            }
            if entry.opt_in_only {
                notes.push("changes things you will notice");
            }
            if !notes.is_empty() {
                out.push_str(&format!("  ({})", notes.join("; ")));
            }
            out.push('\n');
            if self.details {
                out.push_str(&format!("      {}\n", entry.blurb));
            }
        }
        out
    }

    pub fn summary(&self) -> String {
        let chosen = self.chosen();
        if chosen.is_empty() {
            return "Nothing selected -- this produces a profile that loads no mods.".to_string();
        }
        format!(
            "{} selected: {}",
            chosen.len(),
            chosen
                .iter()
                .map(|entry| entry.label)
                .collect::<Vec<_>>()
                .join(", ")
        )
    }
}

impl Default for Picker {
    fn default() -> Self {
        Self::new()
    }
}

pub fn parse_command(line: &str) -> Command {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return Command::Unrecognised(String::new());
    }
    match trimmed.to_ascii_lowercase().as_str() {
        "a" | "accept" | "install" => return Command::Accept,
        "d" | "defaults" => return Command::Defaults,
        "n" | "none" => return Command::None,
        "s" | "all" => return Command::All,
        "l" | "details" => return Command::Details,
        "q" | "quit" | "exit" => return Command::Quit,
        _ => {}
    }
    let rows: Vec<usize> = trimmed
        .split(|c: char| c.is_whitespace() || c == ',')
        .filter(|token| !token.is_empty())
        .filter_map(|token| token.parse::<usize>().ok())
        .collect();
    if rows.is_empty() {
        Command::Unrecognised(trimmed.to_string())
    } else {
        Command::Toggle(rows)
    }
}

const HELP: &str = "\
  Type row numbers to tick or untick them (for example: 1 4 7)
  a  accept and install          d  reset to the recommended set
  s  select everything possible  n  select nothing
  l  show or hide descriptions   q  quit without installing";

/// Drive the picker against a terminal. Returns the chosen set, or `None` if the user quit.
pub fn run(picker: &mut Picker) -> io::Result<Option<Vec<&'static Mod>>> {
    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();
    loop {
        print!("{}", picker.render());
        println!("\n{}\n{HELP}", picker.summary());
        print!("\n> ");
        io::stdout().flush()?;

        let Some(line) = lines.next() else {
            // End of input: a piped run with nothing more to say is a quit, not an install.
            println!();
            return Ok(None);
        };
        match parse_command(&line?) {
            Command::Accept => return Ok(Some(picker.chosen())),
            Command::Quit => return Ok(None),
            Command::Defaults => picker.set_defaults(),
            Command::None => picker.clear(),
            Command::All => {
                let skipped = picker.select_all_compatible();
                if !skipped.is_empty() {
                    println!(
                        "\nLeft off, because each conflicts with something already on: {}",
                        skipped
                            .iter()
                            .map(|entry| entry.label)
                            .collect::<Vec<_>>()
                            .join(", ")
                    );
                }
            }
            Command::Details => picker.details = !picker.details,
            Command::Toggle(rows) => {
                for row in rows {
                    let candidate = picker.catalog_index(row).map(|index| &CATALOG[index]);
                    match picker.toggle(row) {
                        Toggle::OutOfRange => println!("\nThere is no row {row}."),
                        Toggle::Blocked(clashes) => {
                            let candidate = candidate.expect("a blocked row exists");
                            println!();
                            for conflict in clashes {
                                println!("  {}", picker.describe(candidate, conflict));
                            }
                            println!("  Untick the other one first if this is the one you want.");
                        }
                        Toggle::Enabled | Toggle::Disabled => {}
                    }
                }
            }
            Command::Unrecognised(text) => {
                if text.is_empty() {
                    println!("\n{HELP}");
                } else {
                    println!("\nDid not understand {text:?}.\n{HELP}");
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row_of(picker: &Picker, package: &str) -> usize {
        let index = CATALOG
            .iter()
            .position(|entry| entry.package == package)
            .expect("package is in the catalog");
        picker
            .order
            .iter()
            .position(|i| *i == index)
            .expect("row is shown")
            + 1
    }

    #[test]
    fn a_new_picker_starts_on_the_default_set() {
        let picker = Picker::new();
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
    fn every_catalog_row_is_numbered_exactly_once() {
        let picker = Picker::new();
        assert_eq!(picker.order.len(), CATALOG.len());
        let mut seen = picker.order.clone();
        seen.sort_unstable();
        seen.dedup();
        assert_eq!(
            seen.len(),
            CATALOG.len(),
            "a row is shown twice or not at all"
        );
    }

    #[test]
    fn ticking_a_conflicting_row_is_refused_and_changes_nothing() {
        let mut picker = Picker::new();
        picker.clear();
        let product = row_of(&picker, "er-quickload");
        let portrait = row_of(&picker, "er-loading-portrait");

        assert!(matches!(picker.toggle(product), Toggle::Enabled));
        let outcome = picker.toggle(portrait);
        assert!(matches!(outcome, Toggle::Blocked(_)), "expected a refusal");
        let chosen: Vec<_> = picker.chosen().iter().map(|entry| entry.package).collect();
        assert_eq!(
            chosen,
            vec!["er-quickload"],
            "a refused tick changed the set"
        );
    }

    #[test]
    fn unticking_the_other_side_then_ticking_works() {
        let mut picker = Picker::new();
        picker.clear();
        let product = row_of(&picker, "er-quickload");
        let portrait = row_of(&picker, "er-loading-portrait");
        picker.toggle(product);
        assert!(matches!(picker.toggle(product), Toggle::Disabled));
        assert!(matches!(picker.toggle(portrait), Toggle::Enabled));
    }

    #[test]
    fn a_refusal_names_the_other_mod_by_its_label_not_its_package() {
        let mut picker = Picker::new();
        picker.clear();
        picker.toggle(row_of(&picker, "er-quickload"));
        let portrait = selection::by_package("er-loading-portrait").unwrap();
        let clashes = selection::conflicts_with(portrait, &picker.chosen());
        let message = picker.describe(portrait, clashes[0]);
        let product = selection::by_package("er-quickload").unwrap();
        assert!(message.contains(product.label), "message was: {message}");
        assert!(
            !message.contains("er-quickload"),
            "package name leaked: {message}"
        );
    }

    #[test]
    fn select_all_compatible_produces_a_loadable_set() {
        let mut picker = Picker::new();
        let skipped = picker.select_all_compatible();
        let chosen = picker.chosen();
        assert!(
            selection::conflicts_within(&chosen).is_empty(),
            "select-all produced a conflicting set"
        );
        assert!(
            !skipped.is_empty(),
            "with 14 conflict pairs something must be skipped"
        );
        assert_eq!(chosen.len() + skipped.len(), CATALOG.len());
    }

    #[test]
    fn an_out_of_range_row_is_reported_rather_than_panicking() {
        let mut picker = Picker::new();
        assert!(matches!(picker.toggle(0), Toggle::OutOfRange));
        assert!(matches!(
            picker.toggle(CATALOG.len() + 1),
            Toggle::OutOfRange
        ));
    }

    #[test]
    fn commands_parse() {
        assert_eq!(parse_command("a"), Command::Accept);
        assert_eq!(parse_command("  QUIT "), Command::Quit);
        assert_eq!(parse_command("1 4 7"), Command::Toggle(vec![1, 4, 7]));
        assert_eq!(parse_command("2,3"), Command::Toggle(vec![2, 3]));
        assert_eq!(parse_command(""), Command::Unrecognised(String::new()));
        assert_eq!(
            parse_command("yes please"),
            Command::Unrecognised("yes please".to_string())
        );
    }

    #[test]
    fn the_rendered_list_shows_a_row_for_everything_and_marks_the_ticks() {
        let picker = Picker::new();
        let rendered = picker.render();
        for entry in CATALOG {
            assert!(
                rendered.contains(entry.label),
                "{} is not shown",
                entry.package
            );
        }
        let ticked = rendered.matches("[x]").count();
        assert_eq!(ticked, selection::default_selection().len());
    }

    #[test]
    fn rows_that_need_seamless_say_so() {
        let picker = Picker::new();
        let rendered = picker.render();
        let warp = selection::by_package("er-invasion-warp").unwrap();
        let line = rendered
            .lines()
            .find(|line| line.contains(warp.label))
            .expect("the row is shown");
        assert!(line.contains("needs Seamless Co-op"), "line was: {line}");
    }

    #[test]
    fn an_empty_selection_summarises_as_a_clean_profile() {
        let mut picker = Picker::new();
        picker.clear();
        assert!(picker.summary().contains("loads no mods"));
    }
}
