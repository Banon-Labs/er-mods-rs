//! The Code sub-tab (design 9): the selected layer's file, edited in game.
//!
//! # The file stays the source of truth
//!
//! The editor shows the file's text and writes it back [`SAVE_DELAY`] after the last keystroke
//! (design 7.7); the worker then sees the new modification time and compiles, exactly as for an
//! edit made in a Linux editor. While the text is unchanged the file is looked at again every
//! [`POLL_PERIOD`], so an edit made elsewhere shows up here. Line endings are shown as `\n` and a
//! file that had `\r\n` gets them back when it is saved.
//!
//! The file's Windows path is shown, and under Wine its path on the host as well
//! ([`super::files`]).
//!
//! # Counting what the player does
//!
//! The text field is an `InputTextMultiline` with Tab typed into the text and the `ALWAYS` and
//! `EDIT` callbacks on. Each `EDIT` callback adds one to [`super::EDIT_CALLBACKS`], and Ctrl+V or
//! Shift+Insert while the field has the keyboard adds one to [`super::PASTES`]; both reach the
//! telemetry file, which is what the P0.2b proof reads. The callbacks run inside Dear ImGui's C
//! code, where a panic cannot unwind, so they do nothing that can panic: an atomic add, and
//! moving the cursor.
//!
//! # Diagnostics
//!
//! Below the text, `diag/<id>.txt` -- the last messages the worker wrote for this layer -- is
//! listed. Clicking one that names a line of this file puts the cursor on it.

use std::ops::Range;
use std::time::Duration;

/// How long after the last keystroke the file is written (design 7.7).
pub const SAVE_DELAY: Duration = Duration::from_millis(300);
/// How often an unchanged file is looked at again for edits made elsewhere.
pub const POLL_PERIOD: Duration = Duration::from_millis(500);
/// The shortest the text field gets, in lines.
pub const MIN_EDITOR_LINES: f32 = 8.0;
/// Lines given to the diagnostics list below it.
pub const DIAGNOSTIC_LINES: f32 = 6.0;

/// `text` as the editor shows it: every `\r\n` made `\n`. The flag says whether the file had any,
/// so [`to_file`] can put them back.
#[must_use]
pub fn to_editor(text: &str) -> (String, bool) {
    if text.contains("\r\n") {
        (text.replace("\r\n", "\n"), true)
    } else {
        (text.to_owned(), false)
    }
}

/// The editor's `text` as the file holds it: with `\r\n` again when the file had them.
#[must_use]
pub fn to_file(text: &str, crlf: bool) -> String {
    if crlf {
        text.replace('\n', "\r\n")
    } else {
        text.to_owned()
    }
}

/// The bytes of 1-based line `line` of `text`, without its line break.
#[must_use]
pub fn line_span(text: &str, line: u32) -> Option<Range<usize>> {
    let index = usize::try_from(line.checked_sub(1)?).ok()?;
    let mut start = 0;
    for (at, current) in text.split('\n').enumerate() {
        if at == index {
            return Some(start..start + current.len());
        }
        start += current.len() + 1;
    }
    None
}

/// Where the cursor goes for a diagnostic at `line` and 1-based byte `column` of `text`: that
/// column, kept inside the line and moved back to the start of the character it falls in. The
/// line's start without a column.
#[must_use]
pub fn jump_target(text: &str, line: u32, column: Option<u32>) -> Option<usize> {
    let span = line_span(text, line)?;
    let offset = column
        .and_then(|column| usize::try_from(column.saturating_sub(1)).ok())
        .unwrap_or(0);
    let mut at = (span.start + offset).min(span.end);
    while !text.is_char_boundary(at) {
        at -= 1;
    }
    Some(at)
}

#[cfg(windows)]
pub(super) use process::CodeState;

/// The editor's state and drawing.
#[cfg(windows)]
mod process {
    use std::path::Path;
    use std::sync::atomic::Ordering;
    use std::time::Instant;

    use hudhook::imgui::{
        InputTextCallbackHandler, InputTextMultilineCallback, Key, StyleColor, TextCallbackData, Ui,
    };

    use super::super::files::{self, Job, Reply, Written};
    use super::super::layers_tab::{Light, light};
    use super::super::{EDIT_CALLBACKS, PASTES, status_dot};
    use super::{
        DIAGNOSTIC_LINES, MIN_EDITOR_LINES, POLL_PERIOD, SAVE_DELAY, jump_target, to_editor,
        to_file,
    };
    use crate::editor::diagnostics::{self, Diagnostic, Severity};
    use crate::editor::project::{self, Layer, Project};
    use crate::editor::worker::{self, LayerState, Stamp};

    /// Whether the open file can be edited.
    enum Load {
        Reading,
        Ready,
        /// Why not: not UTF-8, a NUL in it, unreadable.
        Refused(String),
    }

    /// The text field's callbacks: the edit count, and a cursor move asked for by a click on a
    /// diagnostic, which applies once the field has the keyboard.
    struct Callbacks<'a> {
        jump: &'a mut Option<usize>,
    }

    impl InputTextCallbackHandler for Callbacks<'_> {
        fn on_edit(&mut self, _: TextCallbackData) {
            EDIT_CALLBACKS.fetch_add(1, Ordering::Relaxed);
        }

        fn on_always(&mut self, mut data: TextCallbackData) {
            if let Some(at) = self.jump.take() {
                data.set_cursor_pos(at);
                let at = i32::try_from(at).unwrap_or(i32::MAX);
                *data.selection_start_mut() = at;
                *data.selection_end_mut() = at;
            }
        }
    }

    /// The Code sub-tab's state: one layer's file at a time.
    pub(in crate::menu) struct CodeState {
        /// The layer whose file is open, and that file's name.
        open: Option<(u32, String)>,
        text: String,
        /// The text as last read or written: what the file holds as far as the panel knows.
        saved: String,
        crlf: bool,
        stamp: Option<Stamp>,
        load: Load,
        /// When the text last changed, while it is unsaved.
        dirty_since: Option<Instant>,
        polled: Option<Instant>,
        windows_path: String,
        unix_path: Option<String>,
        /// A write is on its way to the disk.
        saving: bool,
        save_error: Option<String>,
        /// `diag/<id>.txt`, read: its diagnostics, and the lines that are not one.
        diagnostics: Vec<Diagnostic>,
        other_lines: Vec<String>,
        /// The layer state the diagnostics were read for; a new state reads them again.
        diag_for: Option<Option<LayerState>>,
        diag_polled: Option<Instant>,
        jump: Option<usize>,
        focus: bool,
    }

    impl CodeState {
        pub(in crate::menu) fn new() -> Self {
            Self {
                open: None,
                text: String::new(),
                saved: String::new(),
                crlf: false,
                stamp: None,
                load: Load::Reading,
                dirty_since: None,
                polled: None,
                windows_path: String::new(),
                unix_path: None,
                saving: false,
                save_error: None,
                diagnostics: Vec::new(),
                other_lines: Vec::new(),
                diag_for: None,
                diag_polled: None,
                jump: None,
                focus: false,
            }
        }

        /// The layer whose file is open.
        pub(in crate::menu) fn open_id(&self) -> Option<u32> {
            self.open.as_ref().map(|(id, _)| *id)
        }

        /// Has the text changed since it was last written?
        pub(in crate::menu) fn is_dirty(&self) -> bool {
            self.dirty_since.is_some()
        }

        /// Open `layer`'s file, writing the one open before if it has unsaved changes.
        pub(in crate::menu) fn open(&mut self, layer: &Layer, dir: &Path) {
            let wanted = (layer.id, layer.file.clone());
            if self.open.as_ref() == Some(&wanted) {
                return;
            }
            self.flush(dir);
            *self = Self::new();
            let path = dir.join(project::LAYERS_DIRECTORY).join(&layer.file);
            self.windows_path = path.display().to_string();
            self.open = Some(wanted);
            files::send(Job::Load {
                id: layer.id,
                file: layer.file.clone(),
                path,
                known: None,
            });
            self.read_diagnostics(dir, layer.id);
        }

        /// Close the file, writing it first if it has unsaved changes.
        pub(in crate::menu) fn close(&mut self, dir: &Path) {
            self.flush(dir);
            *self = Self::new();
        }

        /// Write the text now if it has changed.
        pub(in crate::menu) fn flush(&mut self, dir: &Path) {
            let (Some((id, file)), Some(_)) = (self.open.clone(), self.dirty_since) else {
                return;
            };
            self.dirty_since = None;
            self.saved.clone_from(&self.text);
            self.saving = files::send(Job::Write {
                path: dir.join(project::LAYERS_DIRECTORY).join(&file),
                text: to_file(&self.text, self.crlf),
                what: Written::Layer { id, file },
            });
            if !self.saving {
                self.save_error = Some("the panel's file thread is not running".to_owned());
            }
        }

        fn read_diagnostics(&mut self, dir: &Path, id: u32) {
            self.diag_polled = Some(Instant::now());
            files::send(Job::Diag {
                id,
                path: dir.join(project::DIAG_DIRECTORY).join(format!("{id}.txt")),
            });
        }

        /// Once a frame: write the text [`SAVE_DELAY`] after the last change, look at an unchanged
        /// file again every [`POLL_PERIOD`], and read the diagnostics again when the layer's state
        /// moves. `state` is the open layer's.
        pub(in crate::menu) fn tick(&mut self, dir: &Path, state: Option<&LayerState>) {
            let Some((id, file)) = self.open.clone() else {
                return;
            };
            let now = Instant::now();
            if self
                .dirty_since
                .is_some_and(|since| now.duration_since(since) >= SAVE_DELAY)
            {
                self.flush(dir);
            }
            let due =
                |last: Option<Instant>| last.is_none_or(|at| now.duration_since(at) >= POLL_PERIOD);
            if self.dirty_since.is_none()
                && !self.saving
                && matches!(self.load, Load::Ready)
                && due(self.polled)
            {
                self.polled = Some(now);
                let path = dir.join(project::LAYERS_DIRECTORY).join(&file);
                files::send(Job::Load {
                    id,
                    file,
                    path,
                    known: self.stamp,
                });
            }
            let state = state.cloned();
            if self.diag_for.as_ref() != Some(&state) || due(self.diag_polled) {
                self.diag_for = Some(state);
                self.read_diagnostics(dir, id);
            }
        }

        fn is_open(&self, id: u32, file: &str) -> bool {
            self.open
                .as_ref()
                .is_some_and(|(open_id, open_file)| *open_id == id && open_file == file)
        }

        /// An answer from the file thread about this file. Returns whether it was one.
        pub(in crate::menu) fn on_reply(&mut self, reply: &Reply) -> bool {
            match reply {
                Reply::Loaded {
                    id,
                    file,
                    stamp,
                    text,
                    unix_path,
                } if self.is_open(*id, file) => {
                    self.stamp = *stamp;
                    if unix_path.is_some() {
                        self.unix_path.clone_from(unix_path);
                    }
                    match text {
                        Ok(text) => {
                            let (shown, crlf) = to_editor(text);
                            // An edit not yet written wins over the file: it is written next.
                            if self.dirty_since.is_none() && shown != self.saved {
                                self.text.clone_from(&shown);
                                self.saved = shown;
                                self.crlf = crlf;
                            }
                            self.load = Load::Ready;
                        }
                        Err(why) => self.load = Load::Refused(why.clone()),
                    }
                    true
                }
                Reply::Unchanged { id, file } => self.is_open(*id, file),
                Reply::Wrote {
                    what: Written::Layer { id, file },
                    result,
                } if self.is_open(*id, file) => {
                    self.saving = false;
                    self.save_error = result.as_ref().err().cloned();
                    // The next look at the file reads the stamp this write left.
                    self.polled = None;
                    true
                }
                Reply::Diag { id, text } if self.open_id() == Some(*id) => {
                    let text = text.clone().unwrap_or_default();
                    self.diagnostics = diagnostics::parse(&text);
                    self.other_lines = text
                        .lines()
                        .filter(|line| !line.trim().is_empty())
                        .filter(|line| diagnostics::parse_line(line).is_none())
                        .map(str::to_owned)
                        .collect();
                    true
                }
                _ => false,
            }
        }

        /// The sub-tab: paths, the text, Compile now, the layer's state and its diagnostics.
        pub(in crate::menu) fn draw(
            &mut self,
            ui: &Ui,
            project: &Project,
            layer: &Layer,
            state: Option<&LayerState>,
            dir: &Path,
        ) {
            self.open(layer, dir);
            ui.text_disabled(&self.windows_path);
            if let Some(unix) = &self.unix_path {
                ui.text_disabled(unix);
            }
            match &self.load {
                Load::Reading => ui.text_disabled("Reading the file..."),
                Load::Refused(why) => {
                    let _colour = ui.push_style_color(StyleColor::Text, super::super::ERROR_TEXT);
                    ui.text_wrapped(why);
                }
                Load::Ready => self.draw_text(ui),
            }

            if ui.button("Compile now") {
                self.flush(dir);
                worker::request_rescan();
            }
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text("Writes the file now and has the worker read every layer again.");
            }
            ui.same_line();
            let (lamp, _) = light(project, layer, state);
            status_dot(ui, lamp);
            ui.same_line();
            match state {
                Some(state) => ui.text_wrapped(state.label()),
                None => ui.text_disabled("not read by the worker yet"),
            }
            let saved = if let Some(why) = &self.save_error {
                format!("not saved: {why}")
            } else if self.dirty_since.is_some() || self.saving {
                "saving...".to_owned()
            } else {
                "saved 300 ms after your last keystroke".to_owned()
            };
            ui.text_disabled(saved);
            self.draw_diagnostics(ui, &layer.file, lamp);
        }

        fn draw_text(&mut self, ui: &Ui) {
            let line = ui.text_line_height_with_spacing();
            let footer = ui.frame_height_with_spacing() * 2.0 + line * (DIAGNOSTIC_LINES + 1.0);
            let avail = ui.content_region_avail();
            let height = (avail[1] - footer).max(line * MIN_EDITOR_LINES);
            // Escape puts a Dear ImGui text field back to the text it had when it took the
            // keyboard: in a code editor, every edit since the player clicked in, which would then
            // be saved. The field is left out of the frame Escape goes down in, which takes the
            // keyboard from it with no revert; its text is already in `self.text`.
            if ui.is_key_pressed(Key::Escape) {
                ui.dummy([avail[0].max(1.0), height]);
                return;
            }
            if self.focus {
                ui.set_keyboard_focus_here();
                self.focus = false;
            }
            let edited = ui
                .input_text_multiline("##code", &mut self.text, [avail[0], height])
                .allow_tab_input(true)
                .callback(
                    InputTextMultilineCallback::ALWAYS | InputTextMultilineCallback::EDIT,
                    Callbacks {
                        jump: &mut self.jump,
                    },
                )
                .build();
            if ui.is_item_active() {
                let io = ui.io();
                let paste = (io.key_ctrl && ui.is_key_pressed_no_repeat(Key::V))
                    || (io.key_shift && ui.is_key_pressed_no_repeat(Key::Insert));
                if paste {
                    PASTES.fetch_add(1, Ordering::Relaxed);
                }
            }
            if edited && self.text != self.saved {
                self.dirty_since = Some(Instant::now());
            }
        }

        fn draw_diagnostics(&mut self, ui: &Ui, file: &str, lamp: Light) {
            let height = ui.text_line_height_with_spacing() * DIAGNOSTIC_LINES;
            let Some(_list) = ui
                .child_window("##diagnostics")
                .size([0.0, height])
                .border(true)
                .begin()
            else {
                return;
            };
            if self.diagnostics.is_empty() && self.other_lines.is_empty() {
                ui.text_disabled(if lamp == Light::Live {
                    "No messages."
                } else {
                    "No messages yet."
                });
                return;
            }
            let mut jump = None;
            for (at, diagnostic) in self.diagnostics.iter().enumerate() {
                let rows = std::iter::once((diagnostic, false))
                    .chain(diagnostic.notes.iter().map(|note| (note, true)));
                for (index, (shown, is_note)) in rows.enumerate() {
                    let colour = match shown.severity {
                        Severity::Error => super::super::ERROR_TEXT,
                        Severity::Warning => super::super::WARNING_TEXT,
                        Severity::Note => super::super::QUIET_TEXT,
                    };
                    let _colour = ui.push_style_color(StyleColor::Text, colour);
                    let indent = if is_note { "    " } else { "" };
                    if ui.selectable(format!("{indent}{shown}##diag{at}.{index}"))
                        && shown.layer_file() == Some(file)
                        && shown.line > 0
                    {
                        jump = Some((shown.line, shown.column));
                    }
                }
            }
            for line in &self.other_lines {
                ui.text_wrapped(line);
            }
            if let Some((line, column)) = jump
                && let Some(at) = jump_target(&self.text, line, column)
            {
                self.jump = Some(at);
                self.focus = true;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_endings_come_back_as_the_file_had_them() {
        let (shown, crlf) = to_editor("a\r\nb\r\n");
        assert_eq!((shown.as_str(), crlf), ("a\nb\n", true));
        assert_eq!(to_file(&shown, crlf), "a\r\nb\r\n");
        let (shown, crlf) = to_editor("a\nb");
        assert_eq!((shown.as_str(), crlf), ("a\nb", false));
        assert_eq!(to_file(&shown, crlf), "a\nb");
    }

    #[test]
    fn a_diagnostic_puts_the_cursor_on_its_column() {
        let text = "uniform float Strength = 0.6;\nfloat3 lift(float3 c) { return c + Shadow; }\n\
                    \u{e9}t\u{e9}\n";
        assert_eq!(line_span(text, 1), Some(0..29));
        assert_eq!(line_span(text, 2), Some(30..74));
        // Column 36 of line 2 is where `Shadow` starts.
        let at = jump_target(text, 2, Some(36));
        assert_eq!(at.map(|at| &text[at..at + 6]), Some("Shadow"));
        // No column: the start of the line. A column past the end: the end of the line.
        assert_eq!(jump_target(text, 2, None), Some(30));
        assert_eq!(jump_target(text, 1, Some(500)), Some(29));
        // A column inside a two-byte character moves back to its start.
        let third = line_span(text, 3).map_or(0, |span| span.start);
        assert_eq!(jump_target(text, 3, Some(2)), Some(third));
        // Lines that are not there.
        assert_eq!(jump_target(text, 0, None), None);
        assert_eq!(jump_target(text, 9, None), None);
        assert_eq!(line_span(text, 4), Some(text.len()..text.len()));
    }
}
