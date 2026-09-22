//! Offering each mod's own settings file during an install, and editing it without wrecking it.
//!
//! # What this is not allowed to do
//!
//! These files belong to the player and to the DLL that wrote them. Two of them are written by
//! the game itself while it runs -- `er-invasion-warp.toml` has an in-game settings panel, and
//! `er-enemynpc-effects.toml` records its own on/off state -- so a reinstall that replaced a
//! settings file would silently throw away choices made in the game. Nothing here ever replaces
//! an existing file: an edit is an [`upsert`] of one key into the text that is already there,
//! comments and all, and a file nobody chose to change is not opened at all.
//!
//! A file that does not exist yet is the one case where whole text is written, and even then the
//! text is the owning DLL's own boilerplate out of [`crate::settings_schema`] rather than a
//! stripped list of assignments -- because that text is the documentation a player reads later
//! in the game folder, and a file without it teaches them the mod has no settings.
//!
//! # Why the defaults are not written by this program
//!
//! The DLL writes its settings file the first time it finds none, so an install that writes
//! nothing still ends up with a correct file. Writing it here is worth doing anyway: it is what
//! makes the settings visible and editable *before* the first launch, which is the whole point
//! of asking during an install. It is only safe because the bytes are the DLL's own -- see
//! `scripts/gen-installer-settings.py`, which refuses to invent a default.

use std::fmt::Write as _;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::Path;

use crate::catalog::Mod;
use crate::settings_schema::{ConfigFile, Kind, Setting};
use crate::tui::{self, Key};

/// What a run does with the settings files of the mods it installs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Touch no settings file at all. What a reinstall of the DLLs alone wants: the mods are
    /// updated and every setting stays exactly as it was, including the ones changed in game.
    Keep,
    /// Write the shipped file for any mod that does not have one yet, and leave every existing
    /// file alone. No questions asked.
    Defaults,
    /// Offer the settings, file by file. Existing files are included so they can be edited, and
    /// keeping them is one keypress.
    Ask,
}

/// What happened to one settings file.
///
/// No path here on purpose: every one of these files sits beside the game executable, so the
/// caller names that directory once and these names under it.
#[derive(Debug)]
pub struct Outcome {
    pub file: String,
    pub action: Action,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Action {
    /// Already there, and not opened.
    Kept,
    /// Was not there; the owning DLL's own shipped file was written.
    Created,
    /// Was not there; written with the shipped text and the changes made here.
    CreatedWithEdits(usize),
    /// Was there; this many keys were changed in place, comments untouched.
    Edited(usize),
    /// Skipped because this build of the installer has no schema for it.
    Unknown,
}

impl Action {
    /// One line for the end-of-install summary.
    pub fn describe(&self) -> String {
        match self {
            Self::Kept => "left as it is".to_owned(),
            Self::Created => "written with the mod's own defaults".to_owned(),
            Self::CreatedWithEdits(count) => {
                format!("written with the mod's own defaults and {count} change(s)")
            }
            Self::Edited(count) => format!("{count} setting(s) changed in place"),
            Self::Unknown => "not offered: this installer has no settings list for it".to_owned(),
        }
    }
}

// --------------------------------------------------------------------------------------
// Reading and writing the text. Pure, so every rule below is `cargo test`-able.
// --------------------------------------------------------------------------------------

/// Everything before an unquoted `#`.
///
/// The same rule every one of these DLLs' own parsers applies, which is what makes the value
/// this reads back the value the mod will run with.
fn strip_comment(line: &str) -> &str {
    let mut quote: Option<char> = None;
    for (index, char) in line.char_indices() {
        match quote {
            Some(open) if char == open => quote = None,
            Some(_) => {}
            None if char == '"' || char == '\'' => quote = Some(char),
            None if char == '#' => return &line[..index],
            None => {}
        }
    }
    line
}

/// The `[table]` this line opens, if it opens one.
fn table_header(line: &str) -> Option<&str> {
    let trimmed = line.trim();
    trimmed
        .strip_prefix('[')
        .and_then(|rest| rest.strip_suffix(']'))
        .map(str::trim)
}

/// The key this line assigns, and its value.
fn assignment(line: &str) -> Option<(&str, &str)> {
    let (key, value) = strip_comment(line).split_once('=')?;
    let key = key.trim();
    if key.is_empty() || !key.starts_with(|c: char| c.is_ascii_alphabetic() || c == '_') {
        return None;
    }
    Some((key, value.trim()))
}

/// The same, for a line that is entirely inside a comment: `# key = value`.
fn commented_assignment(line: &str) -> Option<(&str, &str)> {
    let body = line.trim().strip_prefix('#')?.trim();
    // `strip_comment` again, so the trailing explanation on `# slot = 0   # the slot` goes.
    let (key, value) = body.split_once('=')?;
    let key = key.trim();
    if key.is_empty() || !key.starts_with(|c: char| c.is_ascii_alphabetic() || c == '_') {
        return None;
    }
    Some((key, strip_comment(value).trim()))
}

/// The value in force for one key, reading the file the way the owning DLL reads it.
///
/// `None` covers both "not mentioned" and "mentioned only as a comment", which are the same
/// thing to the DLL: the key is unset and its built-in default applies.
pub fn value_in_force(text: &str, table: &str, key: &str) -> Option<String> {
    let mut current = "";
    for line in text.lines() {
        if let Some(header) = table_header(line) {
            current = header;
            continue;
        }
        if current != table {
            continue;
        }
        if let Some((found, value)) = assignment(line)
            && found == key
        {
            return Some(value.to_owned());
        }
    }
    None
}

/// Put `key = value` into `text`, keeping every comment that is already there.
///
/// Three cases, in the order they are looked for. An assignment already in the right table is
/// rewritten in place. A commented-out one is replaced by a live assignment, so the author's
/// example line becomes the setting rather than being left above it to contradict it. Otherwise
/// the assignment is inserted at the end of its table's block -- before the next `[header]`, not
/// at the end of the file, because a top-level key written after a table header would belong to
/// that table and mean something else entirely.
pub fn upsert(text: &str, table: &str, key: &str, value: &str) -> String {
    let assignment_line = format!("{key} = {value}");
    let mut lines: Vec<String> = text.lines().map(str::to_owned).collect();
    let mut current = String::new();
    // Where the target table's block ends, so an insert has somewhere to go.
    let mut block_end: Option<usize> = None;
    let mut table_seen = table.is_empty();

    for index in 0..lines.len() {
        if let Some(header) = table_header(&lines[index]) {
            if current == table && block_end.is_none() {
                block_end = Some(index);
            }
            current = header.to_owned();
            if current == table {
                table_seen = true;
            }
            continue;
        }
        if current != table {
            continue;
        }
        if let Some((found, _)) = assignment(&lines[index])
            && found == key
        {
            lines[index] = assignment_line;
            return rejoin(lines, text);
        }
        if let Some((found, _)) = commented_assignment(&lines[index])
            && found == key
        {
            lines[index] = assignment_line;
            return rejoin(lines, text);
        }
    }

    if !table_seen {
        // The table is not in the file. Adding it is correct and is the only case that appends.
        if !lines.last().is_some_and(|line| line.trim().is_empty()) {
            lines.push(String::new());
        }
        lines.push(format!("[{table}]"));
        lines.push(assignment_line);
        return rejoin(lines, text);
    }

    let at = block_end.unwrap_or(lines.len());
    lines.insert(at, assignment_line);
    rejoin(lines, text)
}

/// Take `key` back out of force, leaving the author's line behind as a comment.
///
/// Only reachable for a key that ships commented out, where being absent is a state the DLL is
/// built for. Commenting rather than deleting keeps the documentation in the file, which is the
/// state a fresh install would have been in.
pub fn unset(text: &str, table: &str, key: &str) -> String {
    let mut lines: Vec<String> = text.lines().map(str::to_owned).collect();
    let mut current = String::new();
    for index in 0..lines.len() {
        if let Some(header) = table_header(&lines[index]) {
            current = header.to_owned();
            continue;
        }
        if current != table {
            continue;
        }
        if let Some((found, value)) = assignment(&lines[index])
            && found == key
        {
            lines[index] = format!("# {key} = {value}");
            return rejoin(lines, text);
        }
    }
    text.to_owned()
}

/// Reassemble lines, keeping whether the original ended in a newline.
fn rejoin(lines: Vec<String>, original: &str) -> String {
    let mut out = lines.join("\n");
    if original.ends_with('\n') || original.is_empty() {
        out.push('\n');
    }
    out
}

// --------------------------------------------------------------------------------------
// Values a player types
// --------------------------------------------------------------------------------------

/// Turn what a player typed into the literal a settings file wants, or say why it will not do.
///
/// The type comes from the shipped default, so this refuses exactly what the owning DLL's parser
/// would refuse -- and refusing here costs a retyped line, while letting it through costs a
/// setting that silently does not take. A `text` setting is quoted the way its default is
/// quoted, so a Windows path stays in the single-quoted form where a backslash is literal.
pub fn parse_value(setting: &Setting, typed: &str) -> Result<String, String> {
    let typed = typed.trim();
    if typed.is_empty() {
        return Err("nothing typed".to_owned());
    }
    if !setting.choices.is_empty() {
        let bare = typed.trim_matches(|c| c == '"' || c == '\'');
        if setting.kind != Kind::List
            && !setting
                .choices
                .iter()
                .any(|choice| choice.eq_ignore_ascii_case(bare))
        {
            return Err(format!("not one of {}", setting.choices.join(", ")));
        }
    }
    match setting.kind {
        Kind::Bool => match typed.to_ascii_lowercase().as_str() {
            "true" | "t" | "yes" | "y" | "on" | "1" => Ok("true".to_owned()),
            "false" | "f" | "no" | "n" | "off" | "0" => Ok("false".to_owned()),
            _ => Err("true or false".to_owned()),
        },
        Kind::Int => typed
            .parse::<i64>()
            .map(|number| number.to_string())
            .map_err(|_| "a whole number".to_owned()),
        Kind::Float => typed
            .parse::<f64>()
            .map(|_| {
                // Kept as typed rather than reformatted: `3.0` must not become `3`, which the
                // owning parsers read as an integer and reject.
                if typed.contains('.') {
                    typed.to_owned()
                } else {
                    format!("{typed}.0")
                }
            })
            .map_err(|_| "a number".to_owned()),
        Kind::List => {
            if typed.starts_with('[') && typed.ends_with(']') {
                Ok(typed.to_owned())
            } else {
                // A player typing a list types the items, not the brackets.
                let items: Vec<String> = typed
                    .split(',')
                    .map(str::trim)
                    .filter(|item| !item.is_empty())
                    .map(|item| quote_like(setting.default_item_quote(), item))
                    .collect();
                Ok(format!("[{}]", items.join(", ")))
            }
        }
        Kind::Text => {
            let bare = typed.trim_matches(|c| c == '"' || c == '\'');
            Ok(quote_like(setting.default_quote(), bare))
        }
    }
}

/// Wrap `value` in the quote character the file's own default uses, or none if it uses none.
fn quote_like(quote: Option<char>, value: &str) -> String {
    match quote {
        // Single quotes are TOML's literal strings: no escapes, which is why every Windows path
        // in these files uses them. A value containing one cannot go in them.
        Some('\'') if !value.contains('\'') => format!("'{value}'"),
        Some('\'') => format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\"")),
        Some('"') => format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\"")),
        _ => value.to_owned(),
    }
}

impl Setting {
    /// The quote character this setting's own default is written with, if any.
    fn default_quote(&self) -> Option<char> {
        let first = self.default.chars().next()?;
        (first == '"' || first == '\'').then_some(first)
    }

    /// The same, for the items inside a list default. `[]` tells us nothing, so a quoted guess
    /// is not made: the items go in bare and a numeric list stays numeric.
    fn default_item_quote(&self) -> Option<char> {
        let inner = self
            .default
            .trim_start_matches('[')
            .trim_end_matches(']')
            .trim();
        let first = inner.chars().next()?;
        (first == '"' || first == '\'').then_some(first)
    }

    /// What this setting is right now, for a file that may or may not exist.
    fn current(&self, text: Option<&str>) -> Current {
        match text.and_then(|text| value_in_force(text, self.table, self.key)) {
            Some(value) => Current::InForce(value),
            None if self.optional => Current::Unset,
            None => Current::Default,
        }
    }
}

/// What a settings file says about one key at the moment.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Current {
    /// The file assigns it.
    InForce(String),
    /// The file does not assign it and the DLL's built-in default applies.
    Default,
    /// The file does not assign it, and it ships that way on purpose.
    Unset,
}

impl Current {
    fn show(&self, setting: &Setting) -> String {
        match self {
            Self::InForce(value) => value.clone(),
            Self::Default => format!("{} (default)", setting.default),
            Self::Unset => "unset".to_owned(),
        }
    }
}

// --------------------------------------------------------------------------------------
// The screen
// --------------------------------------------------------------------------------------

/// One settings file, open for editing.
pub struct Editor {
    config: &'static ConfigFile,
    /// The text as it stands: the file's own, or the shipped default for a file not there yet.
    text: String,
    /// True when the file is not on disk, so the summary can say written rather than changed.
    fresh: bool,
    cursor: usize,
    scroll: usize,
    changes: usize,
    message: Option<String>,
    colour: bool,
}

/// How the editor loop should continue.
#[derive(Debug, PartialEq, Eq)]
enum Step {
    Continue,
    /// Finished with this file: write what was changed.
    Done,
    /// Finished with this file and every one after it.
    Abandon,
}

impl Editor {
    pub fn new(config: &'static ConfigFile, existing: Option<String>) -> Self {
        let fresh = existing.is_none();
        Self {
            config,
            text: existing.unwrap_or_else(|| config.default_text.to_owned()),
            fresh,
            cursor: 0,
            scroll: 0,
            changes: 0,
            message: None,
            colour: tui::colour_enabled(),
        }
    }

    fn rows(&self) -> usize {
        self.config.settings.len()
    }

    /// How many settings fit on screen, leaving the header, the detail pane and the key hints.
    fn list_height(height: usize) -> usize {
        height.saturating_sub(9).max(3)
    }

    fn scroll_to_cursor(&mut self, list_height: usize) {
        if self.cursor < self.scroll {
            self.scroll = self.cursor;
        } else if self.cursor >= self.scroll + list_height {
            self.scroll = self.cursor + 1 - list_height;
        }
    }

    fn render(&self, width: usize, height: usize) -> String {
        let list_height = Self::list_height(height);
        let mut out = String::new();
        let state = if self.fresh {
            "not in your game folder yet -- this writes it"
        } else {
            "already in your game folder -- only what you change is touched"
        };
        let _ = writeln!(out, "{}", self.config.file);
        let _ = writeln!(out, "{state}");
        let _ = writeln!(out);

        let settings = self.config.settings;
        let visible = settings
            .iter()
            .enumerate()
            .skip(self.scroll)
            .take(list_height);
        for (index, setting) in visible {
            let current = setting.current(Some(&self.text));
            let marker = if index == self.cursor { '>' } else { ' ' };
            let changed = matches!(&current, Current::InForce(value) if value != setting.default);
            let flag = if changed { '*' } else { ' ' };
            let line = format!(
                "{marker}{flag} {:<34} {}",
                truncate(&setting.path(), 34),
                truncate(&current.show(setting), width.saturating_sub(40))
            );
            if index == self.cursor && self.colour {
                let _ = writeln!(out, "\x1b[7m{}\x1b[0m", truncate(&line, width));
            } else {
                let _ = writeln!(out, "{}", truncate(&line, width));
            }
        }
        for _ in settings.len().saturating_sub(self.scroll)..list_height {
            let _ = writeln!(out);
        }

        let _ = writeln!(out);
        // The prose the author wrote above this key, which is the only explanation of it that
        // exists. Two lines, same as the mod picker's blurb pane.
        let detail: Vec<String> = self.config.settings[self.cursor]
            .prose
            .iter()
            .flat_map(|line| wrap(line.trim_start_matches('#').trim(), width.saturating_sub(2)))
            .collect();
        let mut written = 0;
        for line in detail.iter().take(2) {
            let _ = writeln!(out, " {line}");
            written += 1;
        }
        for _ in written..2 {
            let _ = writeln!(out);
        }

        match &self.message {
            Some(message) => {
                let _ = writeln!(out, "{}", truncate(message, width));
            }
            None => {
                let _ = writeln!(
                    out,
                    "{}",
                    truncate(
                        "enter change   d back to default   u leave unset   n next file   q stop asking",
                        width
                    )
                );
            }
        }
        out
    }

    /// Everything a keypress can do that does not need to read a line of input.
    fn handle(&mut self, key: Key, list_height: usize) -> Step {
        self.message = None;
        match key {
            Key::Up | Key::Char('k') => self.cursor = self.cursor.saturating_sub(1),
            Key::Down | Key::Char('j') => {
                self.cursor = (self.cursor + 1).min(self.rows().saturating_sub(1));
            }
            Key::PageUp => self.cursor = self.cursor.saturating_sub(list_height),
            Key::PageDown => {
                self.cursor = (self.cursor + list_height).min(self.rows().saturating_sub(1));
            }
            Key::Home => self.cursor = 0,
            Key::End => self.cursor = self.rows().saturating_sub(1),
            Key::Char('d') => self.reset_current(),
            Key::Char('u') => self.unset_current(),
            Key::Char('n') | Key::Escape | Key::Right => return Step::Done,
            Key::Char('q') | Key::Interrupt => return Step::Abandon,
            _ => {}
        }
        self.scroll_to_cursor(list_height);
        Step::Continue
    }

    fn current_setting(&self) -> &'static Setting {
        &self.config.settings[self.cursor]
    }

    /// Put the highlighted key back to the value its own file ships.
    fn reset_current(&mut self) {
        let setting = self.current_setting();
        self.text = if setting.optional {
            unset(&self.text, setting.table, setting.key)
        } else {
            upsert(&self.text, setting.table, setting.key, setting.default)
        };
        self.changes += 1;
        self.message = Some(format!("{} back to its shipped value", setting.path()));
    }

    fn unset_current(&mut self) {
        let setting = self.current_setting();
        if !setting.optional {
            self.message = Some(format!(
                "{} is not optional: the mod runs with a value for it either way",
                setting.path()
            ));
            return;
        }
        self.text = unset(&self.text, setting.table, setting.key);
        self.changes += 1;
        self.message = Some(format!("{} left unset", setting.path()));
    }

    /// Set the highlighted key from a line the player typed.
    fn set_current(&mut self, typed: &str) {
        let setting = self.current_setting();
        if typed.trim().is_empty() {
            return;
        }
        match parse_value(setting, typed) {
            Ok(value) => {
                self.text = upsert(&self.text, setting.table, setting.key, &value);
                self.changes += 1;
                self.message = Some(format!("{} = {value}", setting.path()));
            }
            Err(why) => {
                self.message = Some(format!("{}: {why}", setting.path()));
            }
        }
    }

    /// The prompt for the highlighted key, including what it will take.
    fn prompt(&self) -> String {
        let setting = self.current_setting();
        let kind = if !setting.choices.is_empty() {
            format!("one of {}", setting.choices.join(", "))
        } else {
            match setting.kind {
                Kind::Bool => "true or false".to_owned(),
                Kind::Int => "a whole number".to_owned(),
                Kind::Float => "a number".to_owned(),
                Kind::List => "comma-separated".to_owned(),
                Kind::Text => "text".to_owned(),
            }
        };
        format!(
            "{} ({kind}, now {}): ",
            setting.path(),
            setting.current(Some(&self.text)).show(setting)
        )
    }

    /// What to write, and what to call it in the summary. `None` when nothing changed.
    fn result(&self) -> Option<(String, Action)> {
        match (self.fresh, self.changes) {
            (true, 0) => Some((self.text.clone(), Action::Created)),
            (true, count) => Some((self.text.clone(), Action::CreatedWithEdits(count))),
            (false, 0) => None,
            (false, count) => Some((self.text.clone(), Action::Edited(count))),
        }
    }
}

fn truncate(text: &str, width: usize) -> String {
    if text.chars().count() <= width {
        return text.to_owned();
    }
    text.chars().take(width).collect()
}

fn wrap(text: &str, width: usize) -> Vec<String> {
    if width == 0 {
        return Vec::new();
    }
    let mut lines = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        if !line.is_empty() && line.chars().count() + 1 + word.chars().count() > width {
            lines.push(std::mem::take(&mut line));
        }
        if !line.is_empty() {
            line.push(' ');
        }
        line.push_str(word);
    }
    if !line.is_empty() {
        lines.push(line);
    }
    lines
}

// --------------------------------------------------------------------------------------
// Driving it
// --------------------------------------------------------------------------------------

/// The settings files the chosen mods read, each named once, in the order the mods were listed.
///
/// `er-quickload` and `er-build-import` both read `er-quickload.toml`, and the picker refuses
/// that pair anyway -- but a file offered twice would be asked about twice, so the list is
/// deduplicated rather than relying on the conflict table to make it impossible.
pub fn files_for(chosen: &[&'static Mod]) -> Vec<&'static str> {
    let mut files: Vec<&'static str> = Vec::new();
    for entry in chosen {
        if let Some(file) = entry.config
            && !files.contains(&file)
        {
            files.push(file);
        }
    }
    files
}

/// Do whatever `mode` says for every settings file the chosen mods read.
///
/// Writes into `game_dir`, which is where every one of these DLLs looks for its file -- beside
/// the game executable, not beside the DLL.
pub fn run(
    mode: Mode,
    chosen: &[&'static Mod],
    game_dir: &Path,
    interactive: bool,
) -> io::Result<Vec<Outcome>> {
    let files = files_for(chosen);
    if files.is_empty() {
        return Ok(Vec::new());
    }
    let mut outcomes = Vec::with_capacity(files.len());

    if mode == Mode::Keep {
        for file in files {
            outcomes.push(Outcome {
                file: file.to_owned(),
                action: Action::Kept,
            });
        }
        return Ok(outcomes);
    }

    let asking = mode == Mode::Ask && interactive;
    if asking {
        println!(
            "\nSettings. Each mod below reads its own file in the game folder. Existing files \
             are only changed where you change them; the rest are written with the mod's own \
             defaults."
        );
    }

    for file in files {
        let path = game_dir.join(file);
        let existing = fs::read_to_string(&path).ok();
        let Some(config) = ConfigFile::find(file) else {
            outcomes.push(Outcome {
                file: file.to_owned(),
                action: Action::Unknown,
            });
            continue;
        };

        // Not asking: write a file that is not there, and never open one that is.
        if !asking {
            if existing.is_some() {
                outcomes.push(Outcome {
                    file: file.to_owned(),
                    action: Action::Kept,
                });
                continue;
            }
            write(&path, config.default_text)?;
            outcomes.push(Outcome {
                file: file.to_owned(),
                action: Action::Created,
            });
            continue;
        }

        // `drive_screen` takes raw mode itself and falls back to the line-based editor when the
        // terminal will not give it -- so the choice of editor is made per file, by the thing
        // that needs the terminal, rather than guessed once up here.
        let mut editor = Editor::new(config, existing);
        let stopped = drive_screen(&mut editor)?;
        if let Some((text, action)) = editor.result() {
            write(&path, &text)?;
            outcomes.push(Outcome {
                file: file.to_owned(),
                action,
            });
        } else {
            outcomes.push(Outcome {
                file: file.to_owned(),
                action: Action::Kept,
            });
        }
        if stopped {
            // `q`: stop asking, and treat every remaining file the way `--default-configs`
            // would -- write the missing ones, open none of the existing ones.
            let mut rest = run_quietly(chosen, game_dir, &outcomes)?;
            outcomes.append(&mut rest);
            return Ok(outcomes);
        }
    }
    Ok(outcomes)
}

/// The files not yet dealt with, handled the way [`Mode::Defaults`] handles them.
fn run_quietly(
    chosen: &[&'static Mod],
    game_dir: &Path,
    done: &[Outcome],
) -> io::Result<Vec<Outcome>> {
    let mut outcomes = Vec::new();
    for file in files_for(chosen) {
        if done.iter().any(|outcome| outcome.file == file) {
            continue;
        }
        let path = game_dir.join(file);
        let action = match ConfigFile::find(file) {
            None => Action::Unknown,
            Some(_) if path.is_file() => Action::Kept,
            Some(config) => {
                write(&path, config.default_text)?;
                Action::Created
            }
        };
        outcomes.push(Outcome {
            file: file.to_owned(),
            action,
        });
    }
    Ok(outcomes)
}

fn write(path: &Path, text: &str) -> io::Result<()> {
    if let Some(parent) = path.parent()
        && !parent.as_os_str().is_empty()
    {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, text)
}

/// The full-screen editor. Returns true when the player asked to stop being asked.
///
/// # Why the raw-mode guard is owned here and not by the caller
///
/// A value is typed, not chosen from a list, and a line cannot be typed in raw mode: raw mode
/// is exactly the mode in which backspace arrives as a byte nobody handles and `enter` never
/// ends the read. So the guard has to be dropped for the duration of each prompt and taken
/// again afterwards, which only works if this function owns it.
///
/// Getting that wrong is not a cosmetic bug. Measured 2026-09-21 driving this over a pty: the
/// first version entered a *second* guard for the prompt and dropped it immediately -- so the
/// terminal stayed raw, `read_line` waited for a newline that a raw terminal never delivers,
/// and the run hung until the pty was closed and then exited non-zero. The screen also blinked
/// through the alternate-screen switch twice, because entering the guard is what switches it.
fn drive_screen(editor: &mut Editor) -> io::Result<bool> {
    let Some(mut raw) = tui::RawMode::enter() else {
        return drive_plain(editor);
    };
    let mut stdin = io::stdin();
    loop {
        let (width, height) = tui::size();
        tui::paint(&editor.render(width, height))?;
        let Some(key) = tui::next_key(&mut stdin) else {
            return Ok(true);
        };
        if key == Key::Enter || key == Key::Space {
            // Back to the ordinary screen for the prompt, then back into the editor. The value
            // and the question about it are both on the normal screen, where a terminal's own
            // scrollback keeps them.
            drop(raw);
            let typed = read_line(&editor.prompt())?;
            match typed {
                Some(line) => editor.set_current(&line),
                None => return Ok(true),
            }
            match tui::RawMode::enter() {
                Some(taken) => raw = taken,
                // The terminal took raw mode a moment ago and will not now. Finishing this file
                // on the line-based editor is better than dropping the settings the player has
                // already changed, which are held in `editor` either way.
                None => return drive_plain(editor),
            }
            continue;
        }
        match editor.handle(key, Editor::list_height(height)) {
            Step::Continue => {}
            Step::Done => return Ok(false),
            Step::Abandon => return Ok(true),
        }
    }
}

/// Ask one question on the ordinary screen. `None` at end of input.
fn read_line(prompt: &str) -> io::Result<Option<String>> {
    print!("\n{prompt}");
    io::stdout().flush()?;
    let mut line = String::new();
    if io::stdin().lock().read_line(&mut line)? == 0 {
        return Ok(None);
    }
    Ok(Some(line))
}

const PLAIN_HELP: &str = "\
  Type a row number to change that setting, then the value
  d <n>  put row n back to its shipped value    u <n>  leave row n unset
  n      next file                              q      stop asking";

/// The line-based editor, for a terminal that will not go into raw mode and for piped input.
fn drive_plain(editor: &mut Editor) -> io::Result<bool> {
    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();
    loop {
        println!("\n{}", editor.plain_list());
        println!("{PLAIN_HELP}");
        print!("\n> ");
        io::stdout().flush()?;
        let Some(line) = lines.next() else {
            println!();
            return Ok(true);
        };
        let line = line?;
        let trimmed = line.trim();
        let lowered = trimmed.to_ascii_lowercase();
        match lowered.as_str() {
            "" | "n" | "next" => return Ok(false),
            "q" | "quit" | "stop" => return Ok(true),
            _ => {}
        }
        let (command, argument) = match trimmed.split_once(char::is_whitespace) {
            Some((head, tail)) => (head.to_ascii_lowercase(), tail.trim().to_owned()),
            None => (lowered.clone(), String::new()),
        };
        let row_of = |text: &str| -> Option<usize> {
            text.parse::<usize>()
                .ok()
                .filter(|number| *number >= 1 && *number <= editor.rows())
                .map(|number| number - 1)
        };
        match command.as_str() {
            "d" | "u" => match row_of(&argument) {
                Some(row) => {
                    editor.cursor = row;
                    if command == "d" {
                        editor.reset_current();
                    } else {
                        editor.unset_current();
                    }
                    if let Some(message) = &editor.message {
                        println!("{message}");
                    }
                }
                None => println!("{argument:?} is not a row number on this file"),
            },
            _ => match row_of(&command) {
                Some(row) => {
                    editor.cursor = row;
                    let value = if argument.is_empty() {
                        print!("{}", editor.prompt());
                        io::stdout().flush()?;
                        match lines.next() {
                            Some(next) => next?,
                            None => return Ok(true),
                        }
                    } else {
                        argument
                    };
                    editor.set_current(&value);
                    if let Some(message) = &editor.message {
                        println!("{message}");
                    }
                }
                None => println!("{trimmed:?} is not a row number or a command"),
            },
        }
    }
}

impl Editor {
    /// The numbered list the line-based editor shows.
    fn plain_list(&self) -> String {
        let mut out = String::new();
        let state = if self.fresh {
            "not in your game folder yet"
        } else {
            "already in your game folder"
        };
        let _ = writeln!(out, "{} ({state})", self.config.file);
        for (index, setting) in self.config.settings.iter().enumerate() {
            let current = setting.current(Some(&self.text));
            let changed = matches!(&current, Current::InForce(value) if value != setting.default);
            let _ = writeln!(
                out,
                "{:>3}{} {:<34} {}",
                index + 1,
                if changed { "*" } else { " " },
                setting.path(),
                current.show(setting)
            );
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings_schema::CONFIGS;

    fn setting_named(file: &str, path: &str) -> &'static Setting {
        ConfigFile::find(file)
            .expect("the file is in the schema")
            .settings
            .iter()
            .find(|setting| setting.path() == path)
            .expect("the setting is in the schema")
    }

    #[test]
    fn a_live_assignment_is_rewritten_in_place_and_every_comment_stays() {
        let text = "# why this exists\nhotkey = \"ctrl+a\"\n# another note\nother = 1\n";
        assert_eq!(
            upsert(text, "", "hotkey", "\"ctrl+b\""),
            "# why this exists\nhotkey = \"ctrl+b\"\n# another note\nother = 1\n"
        );
    }

    /// The author's commented example becomes the setting. Leaving it above a new assignment
    /// would put two statements of the same key in the file, one of them wrong.
    #[test]
    fn a_commented_key_becomes_the_assignment_rather_than_gaining_a_second_line() {
        let text = "# the slot to load\n# slot = 0    # trailing note\n";
        assert_eq!(
            upsert(text, "", "slot", "3"),
            "# the slot to load\nslot = 3\n"
        );
    }

    /// A top-level key appended after a table header would belong to that table, which is a
    /// different setting -- or no setting at all.
    #[test]
    fn a_new_top_level_key_goes_in_before_the_first_table_not_at_the_end() {
        let text = "existing = 1\n\n[target]\nmode = \"lock_on\"\n";
        assert_eq!(
            upsert(text, "", "added", "2"),
            "existing = 1\n\nadded = 2\n[target]\nmode = \"lock_on\"\n"
        );
    }

    #[test]
    fn a_key_in_a_table_is_found_only_inside_that_table() {
        let text = "mode = \"top\"\n[target]\nmode = \"lock_on\"\n";
        assert_eq!(
            upsert(text, "target", "mode", "\"chr_id\""),
            "mode = \"top\"\n[target]\nmode = \"chr_id\"\n"
        );
        assert_eq!(value_in_force(text, "", "mode").unwrap(), "\"top\"");
        assert_eq!(
            value_in_force(text, "target", "mode").unwrap(),
            "\"lock_on\""
        );
    }

    #[test]
    fn a_table_that_is_not_in_the_file_is_added() {
        let text = "top = 1\n";
        assert_eq!(
            upsert(text, "spawn", "chr_id", "4500"),
            "top = 1\n\n[spawn]\nchr_id = 4500\n"
        );
    }

    /// A commented key is what the file looks like on a fresh install, so putting one back must
    /// leave the documentation behind rather than deleting the line.
    #[test]
    fn unsetting_comments_the_line_out_and_keeps_the_value_as_the_example() {
        let text = "# the slot\nslot = 3\n";
        assert_eq!(unset(text, "", "slot"), "# the slot\n# slot = 3\n");
        assert_eq!(value_in_force(&unset(text, "", "slot"), "", "slot"), None);
    }

    #[test]
    fn a_commented_key_is_not_in_force() {
        assert_eq!(value_in_force("# slot = 0\n", "", "slot"), None);
    }

    /// The quoting has to match the file's own, because these are two different TOML types: a
    /// single-quoted literal keeps a backslash and a basic string eats it.
    #[test]
    fn a_windows_path_keeps_the_single_quotes_its_default_uses() {
        let setting = setting_named("er-quickload.toml", "save_file");
        assert_eq!(
            parse_value(setting, r"C:\Users\me\ER0000.sl2").unwrap(),
            r"'C:\Users\me\ER0000.sl2'"
        );
    }

    #[test]
    fn a_hotkey_keeps_the_double_quotes_its_default_uses() {
        let setting = setting_named("er-refill-all.toml", "gamepad_hotkey");
        assert_eq!(parse_value(setting, "lb+rb").unwrap(), "\"lb+rb\"");
    }

    #[test]
    fn a_value_outside_the_files_own_list_of_choices_is_refused() {
        let setting = setting_named("er-inventory-sort.toml", "armaments");
        assert!(parse_value(setting, "alphabetical").is_err());
        assert_eq!(parse_value(setting, "preserve").unwrap(), "\"preserve\"");
    }

    #[test]
    fn a_whole_number_typed_for_a_float_setting_keeps_its_point() {
        let setting = setting_named("er-invasion-path.toml", "bold_at_meters");
        assert_eq!(parse_value(setting, "30").unwrap(), "30.0");
        assert_eq!(parse_value(setting, "12.5").unwrap(), "12.5");
        assert!(parse_value(setting, "near").is_err());
    }

    #[test]
    fn a_bool_takes_the_words_a_person_types() {
        let setting = setting_named("er-refill-all.toml", "refill_immediately");
        assert_eq!(parse_value(setting, "no").unwrap(), "false");
        assert_eq!(parse_value(setting, "Yes").unwrap(), "true");
        assert!(parse_value(setting, "sometimes").is_err());
    }

    #[test]
    fn a_list_is_typed_as_items_and_comes_out_bracketed_in_the_files_own_quotes() {
        let setting = setting_named("er-quit-menu.toml", "rows");
        assert_eq!(
            parse_value(setting, "load-character, save-game").unwrap(),
            "[\"load-character\", \"save-game\"]"
        );
    }

    /// Every edit must survive being read back by the same rule the owning DLL reads by. This
    /// is the test that covers all nine files rather than the three the cases above name.
    #[test]
    fn every_shipped_default_can_be_set_and_read_back_in_its_own_file() {
        for config in CONFIGS {
            for setting in config.settings {
                let text = upsert(
                    config.default_text,
                    setting.table,
                    setting.key,
                    setting.default,
                );
                assert_eq!(
                    value_in_force(&text, setting.table, setting.key).as_deref(),
                    Some(setting.default),
                    "{}: {} did not read back",
                    config.file,
                    setting.path()
                );
            }
        }
    }

    /// An upsert of the value already in force must be a no-op on the bytes. Otherwise a player
    /// who opens a file, changes nothing and accepts it gets a rewritten file, and the mod's
    /// own hot-reload reports a change that did not happen.
    #[test]
    fn setting_a_key_to_what_it_already_says_changes_nothing() {
        for config in CONFIGS {
            for setting in config.settings.iter().filter(|setting| !setting.optional) {
                assert_eq!(
                    upsert(
                        config.default_text,
                        setting.table,
                        setting.key,
                        setting.default
                    ),
                    config.default_text,
                    "{}: rewriting {} with its own value moved bytes",
                    config.file,
                    setting.path()
                );
            }
        }
    }

    #[test]
    fn one_file_read_by_two_mods_is_offered_once() {
        let chosen: Vec<&'static Mod> = crate::catalog::CATALOG
            .iter()
            .filter(|entry| entry.config == Some("er-quickload.toml"))
            .collect();
        assert!(chosen.len() > 1, "the fixture needs two mods on one file");
        assert_eq!(files_for(&chosen), vec!["er-quickload.toml"]);
    }

    #[test]
    fn keep_mode_opens_nothing_and_still_reports_every_file() {
        let chosen: Vec<&'static Mod> = crate::catalog::CATALOG
            .iter()
            .filter(|entry| entry.package == "er-refill-all")
            .collect();
        let dir = std::env::temp_dir().join(format!("er-settings-keep-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let outcomes = run(Mode::Keep, &chosen, &dir, false).unwrap();
        assert_eq!(outcomes.len(), 1);
        assert_eq!(outcomes[0].action, Action::Kept);
        assert!(
            !dir.join("er-refill-all.toml").exists(),
            "Keep must not create a file"
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn defaults_mode_writes_a_missing_file_and_leaves_an_existing_one_byte_for_byte() {
        let chosen: Vec<&'static Mod> = crate::catalog::CATALOG
            .iter()
            .filter(|entry| entry.package == "er-refill-all")
            .collect();
        let dir = std::env::temp_dir().join(format!("er-settings-def-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        let outcomes = run(Mode::Defaults, &chosen, &dir, false).unwrap();
        assert_eq!(outcomes[0].action, Action::Created);
        let written = fs::read_to_string(dir.join("er-refill-all.toml")).unwrap();
        assert_eq!(
            written,
            ConfigFile::find("er-refill-all.toml").unwrap().default_text,
            "a created file must be the DLL's own text, comments and all"
        );

        // The second run is the reinstall this mode exists for.
        let edited = written.replace("select+start", "lb+rb");
        fs::write(dir.join("er-refill-all.toml"), &edited).unwrap();
        let again = run(Mode::Defaults, &chosen, &dir, false).unwrap();
        assert_eq!(again[0].action, Action::Kept);
        assert_eq!(
            fs::read_to_string(dir.join("er-refill-all.toml")).unwrap(),
            edited,
            "a reinstall must not touch a settings file that is already there"
        );
        let _ = fs::remove_dir_all(&dir);
    }

    /// `Ask` with no terminal must behave as `Defaults` rather than blocking on a prompt
    /// nobody can answer -- a piped run is how this is scripted and tested.
    #[test]
    fn ask_mode_without_a_terminal_falls_back_to_writing_only_missing_files() {
        let chosen: Vec<&'static Mod> = crate::catalog::CATALOG
            .iter()
            .filter(|entry| entry.package == "er-refill-all")
            .collect();
        let dir = std::env::temp_dir().join(format!("er-settings-ask-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let outcomes = run(Mode::Ask, &chosen, &dir, false).unwrap();
        assert_eq!(outcomes[0].action, Action::Created);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_mod_with_no_settings_file_produces_no_outcome() {
        let chosen: Vec<&'static Mod> = crate::catalog::CATALOG
            .iter()
            .filter(|entry| entry.package == "er-armament-icons")
            .collect();
        assert!(files_for(&chosen).is_empty());
        let dir = std::env::temp_dir().join("er-settings-none");
        assert!(
            run(Mode::Defaults, &chosen, &dir, false)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn the_editor_reports_what_it_would_write() {
        let config = ConfigFile::find("er-refill-all.toml").unwrap();
        let mut editor = Editor::new(config, None);
        assert_eq!(
            editor.result().map(|(_, action)| action),
            Some(Action::Created)
        );
        editor.set_current("lb+rb");
        let (text, action) = editor.result().unwrap();
        assert_eq!(action, Action::CreatedWithEdits(1));
        assert!(text.contains("gamepad_hotkey = \"lb+rb\""));

        let existing = Editor::new(config, Some(config.default_text.to_owned()));
        assert!(
            existing.result().is_none(),
            "an untouched existing file must not be rewritten"
        );
    }

    /// The prose pane is the only explanation of a setting a player gets, so a setting with
    /// none is a setting offered blind.
    #[test]
    fn every_setting_carries_the_prose_its_author_wrote() {
        let mut bare = Vec::new();
        for config in CONFIGS {
            for setting in config.settings {
                if setting.prose.is_empty() {
                    bare.push(format!("{}:{}", config.file, setting.path()));
                }
            }
        }
        assert!(bare.is_empty(), "settings with no explanation: {bare:?}");
    }
}
