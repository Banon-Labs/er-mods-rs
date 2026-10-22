//! The Code sub-tab (design 9, the canvas's `Code.dc.html`): the selected layer's file, edited in
//! game.
//!
//! # The file stays the source of truth
//!
//! The editor shows the file's text and writes it back [`SAVE_DELAY`] after the last keystroke
//! (design 7.7); the worker then sees the new modification time and compiles, exactly as for an
//! edit made in a Linux editor. While the text is unchanged the file is looked at again every
//! [`POLL_PERIOD`], so an edit made elsewhere shows up here. Line endings are shown as `\n` and a
//! file that had `\r\n` gets them back when it is saved.
//!
//! The file's Windows path is shown with the game's folder written `<Game>`, and under Wine its
//! path on the host as well ([`super::files`]); a path wider than the panel loses components from
//! its front ([`elide_front`]).
//!
//! # Drawn by the panel, typed into Dear ImGui's field
//!
//! The code area the canvas shows -- line numbers in a gutter, syntax colours ([`highlight`]), a
//! red band on the line the first error is on and a wave under the name it names -- is beyond
//! Dear ImGui's text field, which sets all its text in one colour and steps its lines by the
//! font's size. So the field keeps what it does well, the typing, the selection, the clipboard
//! and the undo, with everything it would draw made clear, and the panel draws the code under it
//! from the same text at the same positions:
//!
//! - The field is set in a copy of the code font whose own size is the line height over the
//!   window's scales. Dear ImGui scales a glyph by the size it draws at over the font's own size,
//!   so the glyphs keep their size while every row the field measures, clicks into and selects is
//!   [`super::theme::CODE_LINE_HEIGHT`] tall. The host's font is never written to.
//! - It scrolls itself up and down as it always did, so Page Up, Page Down and Ctrl with the
//!   arrows keep their meaning. The panel reads that scroll back after the field each frame by
//!   beginning the field's child window a second time, which Dear ImGui calls appending to it.
//!   The second begin passes `NavFlattened`, which keeps it from taking the keyboard back from a
//!   field that keyboard navigation has just activated.
//! - Sideways it never scrolls (`NoHorizontalScroll`). The panel moves the whole field left by
//!   its own sideways scroll instead, clipped to the code area, keeps the cursor in view the way
//!   Dear ImGui would ([`follow`]), and takes the sideways wheel.
//! - The cursor and the selection come from the field's callbacks, which report them on every
//!   frame the field has the keyboard, and are drawn with the text, the selection under it.
//!
//! # Completion
//!
//! While the field has the keyboard, a popup under the word being typed lists what can go there
//! ([`crate::editor::complete`]): it opens on a `.`, a `<` in a uniform's annotations, the quote
//! after `ui_type =`, a name two characters long, or Ctrl+Space, and follows the word until it is
//! left. Up and Down move its selection, Tab or Enter writes the selected item, Escape closes it.
//! The keys are read before the field is drawn and acted on in its callbacks, which run after the
//! field has handled them:
//!
//! - Tab and Enter reach the field as a tab or a line break through its character filter, which
//!   drops the one the accepting key brings; the callback then makes the item's edits.
//! - Up and Down move the field's cursor a line, and the callback puts it back where it was.
//! - Escape still keeps the frame from the field, so it cannot put its text back; with the popup
//!   open it also asks for the keyboard back on the next frame at the cursor it had, as a click on
//!   a compiler message does.
//!
//! The popup is drawn on the foreground draw list rather than in a window, so it never takes the
//! keyboard from the field, under the word's line or over it when there is no room below, and not
//! while that line is scrolled out of the code area. Its rectangle is recorded as
//! `completion_popup` while it shows.
//!
//! # Reference
//!
//! Reference (F1), beside Compile now, opens a column at the code area's right listing everything
//! a layer's code can use ([`super::reference_panel`]), which the code area narrows for. An entry
//! clicked there, or written from its search field with Enter, goes in through the field's
//! callback like an accepted completion: the click gives the field the keyboard back with its
//! cursor where it was, and the next callback makes the entry's edits
//! (`crate::editor::reference::insertion`) with the same checks.
//!
//! # Counting what the player does
//!
//! The text field is an `InputTextMultiline` with Tab typed into the text and the `ALWAYS`, `EDIT`
//! and character filter callbacks on. Each `EDIT` callback adds one to [`super::EDIT_CALLBACKS`],
//! and Ctrl+V or Shift+Insert while the field has the keyboard adds one to [`super::PASTES`]; a
//! completion popup opening adds one to [`super::COMPLETION_POPUPS`] and an accepted item one to
//! [`super::COMPLETIONS_ACCEPTED`]. All reach the telemetry file, which is what the P0.2b proof
//! reads, and each accepted item is logged. The callbacks run inside Dear ImGui's C code, where a
//! panic cannot unwind, so they do nothing that can panic: atomic adds, copying the cursor out,
//! moving the cursor, the completion engine (tested at every offset of a text not to panic), and
//! edits checked to land on the text's characters before they are made.
//!
//! # Compiler messages
//!
//! Below the text, `diag/<id>.txt` -- the last messages the worker wrote for this layer -- is
//! listed in a card. Clicking one that names a line of this file puts the cursor on it.

use std::ops::Range;
use std::time::Duration;

use crate::editor::diagnostics::{Diagnostic, Severity};
use crate::editor::worker::LayerState;

/// How long after the last keystroke the file is written (design 7.7).
pub const SAVE_DELAY: Duration = Duration::from_millis(300);
/// How often an unchanged file is looked at again for edits made elsewhere.
pub const POLL_PERIOD: Duration = Duration::from_millis(500);
/// The shortest the code area gets, in lines.
pub const MIN_EDITOR_LINES: f32 = 8.0;
/// The most lines the compiler messages card shows before it scrolls.
pub const DIAGNOSTIC_LINES: f32 = 6.0;

// Sizes in pixels at 1080p, from `Code.dc.html`, that only this sub-tab draws.

/// The gutter's padding on its right, between the numbers and its rule.
pub const GUTTER_PAD_RIGHT: f32 = 8.0;
/// Between the Windows path and the host path.
pub const PATH_GAP: f32 = 2.0;
/// Between the action row's parts, and above a part it wraps onto a line of its own.
pub const ACTION_GAP: f32 = 10.0;
/// Between the status light and the state beside it.
pub const STATUS_GAP: f32 = 8.0;
/// Between two compiler messages.
pub const MESSAGE_GAP: f32 = 4.0;
/// How far a note is set in under the message it explains.
pub const NOTE_INDENT: f32 = 18.0;
/// The wave under an error's name, below the baseline: CSS's `text-underline-offset`.
pub const WAVE_DROP: f32 = 3.0;
/// How far above a message line's bottom a link's underline sits.
pub const LINK_UNDERLINE: f32 = 2.0;
/// Dear ImGui's cursor blink: a period, and the part of it the cursor shows.
pub const BLINK_PERIOD: f64 = 1.2;
pub const BLINK_ON: f64 = 0.8;

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

/// Every line of `text`, split at `\n`, as its bytes without the break. An empty text is one
/// empty line, as the field shows it.
#[must_use]
pub fn line_ranges(text: &str) -> Vec<Range<usize>> {
    let mut start = 0;
    text.split('\n')
        .map(|line| {
            let range = start..start + line.len();
            start = range.end + 1;
            range
        })
        .collect()
}

/// What a run of code is coloured as.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Syntax {
    Plain,
    Keyword,
    Type,
    String,
    Comment,
}

/// The words coloured as keywords: the ones a layer file is written with, `uniform` to `struct`,
/// and the rest of HLSL's control flow, which would look like a mistake left plain next to a
/// coloured `for`.
pub const KEYWORDS: [&str; 19] = [
    "uniform", "return", "if", "else", "for", "while", "static", "const", "in", "out", "inout",
    "struct", "do", "break", "continue", "switch", "case", "default", "discard",
];

/// The scalar types a vector or matrix type is named from: `float`, `float3`, `float4x4`.
pub const SCALAR_TYPES: [&str; 12] = [
    "bool",
    "int",
    "uint",
    "dword",
    "half",
    "float",
    "double",
    "min16float",
    "min10float",
    "min16int",
    "min12int",
    "min16uint",
];

/// HLSL's own type names that are not built from a scalar. The contexts a layer's `apply` is
/// handed, `TonemapCtx` and the like, are names the editor declares, and the canvas leaves them
/// plain.
pub const NAMED_TYPES: [&str; 3] = ["void", "matrix", "vector"];

/// What an identifier is coloured as, on its own; [`highlight`] leaves a type name plain where it
/// is called as a constructor.
#[must_use]
pub fn classify(word: &str) -> Syntax {
    let dimension = |digit: u8| (b'1'..=b'4').contains(&digit);
    let shaped = |rest: &str| match rest.as_bytes() {
        [] => true,
        [n] => dimension(*n),
        [n, b'x', m] => dimension(*n) && dimension(*m),
        _ => false,
    };
    if KEYWORDS.contains(&word) {
        Syntax::Keyword
    } else if NAMED_TYPES.contains(&word)
        || SCALAR_TYPES
            .iter()
            .any(|scalar| word.strip_prefix(scalar).is_some_and(shaped))
    {
        Syntax::Type
    } else {
        Syntax::Plain
    }
}

/// Add `range` as `kind` to `runs`, joined to the run before it when that is the same kind.
fn push_run(runs: &mut Vec<(Range<usize>, Syntax)>, range: Range<usize>, kind: Syntax) {
    if range.is_empty() {
        return;
    }
    match runs.last_mut() {
        Some((last, last_kind)) if *last_kind == kind && last.end == range.start => {
            last.end = range.end;
        }
        _ => runs.push((range, kind)),
    }
}

/// Where the string starting at the `"` at `at` ends: after its closing quote, or at the line's
/// end. A backslash escapes the character after it.
fn string_end(line: &str, at: usize) -> usize {
    let mut escaped = false;
    for (offset, c) in line.get(at + 1..).unwrap_or("").char_indices() {
        if escaped {
            escaped = false;
        } else if c == '\\' {
            escaped = true;
        } else if c == '"' {
            return at + 1 + offset + 1;
        }
    }
    line.len()
}

/// Where a run of the bytes `keep` takes, from `at`, ends.
fn run_end(line: &str, at: usize, keep: impl Fn(u8) -> bool) -> usize {
    at + line.as_bytes()[at..]
        .iter()
        .take_while(|b| keep(**b))
        .count()
}

/// One line's runs, in its own bytes; `in_comment` carries a block comment from line to line.
fn highlight_line(line: &str, in_comment: &mut bool) -> Vec<(Range<usize>, Syntax)> {
    let mut runs = Vec::new();
    let mut at = 0;
    while at < line.len() {
        let rest = &line.as_bytes()[at..];
        let (end, kind) = if *in_comment || rest.starts_with(b"/*") {
            let from = if *in_comment { at } else { at + 2 };
            *in_comment = true;
            let end = match line[from..].find("*/") {
                Some(found) => {
                    *in_comment = false;
                    from + found + 2
                }
                None => line.len(),
            };
            (end, Syntax::Comment)
        } else if rest.starts_with(b"//") {
            (line.len(), Syntax::Comment)
        } else if rest[0] == b'"' {
            (string_end(line, at), Syntax::String)
        } else if rest[0].is_ascii_alphabetic() || rest[0] == b'_' {
            let end = run_end(line, at, |b| b.is_ascii_alphanumeric() || b == b'_');
            // A type name with `(` straight after it is a constructor call, `float3(0.0, ...)`,
            // which the canvas leaves plain; it colours a type only where one is declared.
            let kind = match classify(&line[at..end]) {
                Syntax::Type if line.as_bytes().get(end) == Some(&b'(') => Syntax::Plain,
                kind => kind,
            };
            (end, kind)
        } else if rest[0].is_ascii_digit() {
            let end = run_end(line, at, |b| b.is_ascii_alphanumeric() || b == b'.');
            (end, Syntax::Plain)
        } else {
            let width = line[at..].chars().next().map_or(1, char::len_utf8);
            (at + width, Syntax::Plain)
        };
        push_run(&mut runs, at..end, kind);
        at = end;
    }
    runs
}

/// Each line of `text` ([`line_ranges`]) as the runs of one colour that cover it, in bytes of
/// `text`: keywords, types where one is declared, strings and `//` and `/* */` comments, the rest
/// plain.
#[must_use]
pub fn highlight(text: &str) -> Vec<Vec<(Range<usize>, Syntax)>> {
    let mut in_comment = false;
    line_ranges(text)
        .into_iter()
        .map(|line| {
            let base = line.start;
            highlight_line(&text[line], &mut in_comment)
                .into_iter()
                .map(|(run, kind)| (base + run.start..base + run.end, kind))
                .collect()
        })
        .collect()
}

/// The bytes the wave goes under for a diagnostic at `line` and `column` of `text`: the name or
/// number that starts there, else the one character there. `None` without a column, at the end of
/// the line, or past the text.
#[must_use]
pub fn error_span(text: &str, line: u32, column: Option<u32>) -> Option<Range<usize>> {
    column?;
    let span = line_span(text, line)?;
    let start = jump_target(text, line, column)?;
    let rest = text.get(start..span.end)?;
    let word = rest
        .bytes()
        .take_while(|b| b.is_ascii_alphanumeric() || *b == b'_')
        .count();
    let len = if word > 0 {
        word
    } else {
        rest.chars().next()?.len_utf8()
    };
    Some(start..start + len)
}

/// The first error in `diagnostics` about the layer file `file`, as the 0-based line of `text` it
/// is on and the bytes its wave goes under ([`error_span`]). `None` when there is none, or its
/// line is past the end of `text`.
#[must_use]
pub fn first_error(
    diagnostics: &[Diagnostic],
    file: &str,
    text: &str,
) -> Option<(usize, Option<Range<usize>>)> {
    let found = diagnostics.iter().find(|diagnostic| {
        diagnostic.severity == Severity::Error
            && diagnostic.line > 0
            && diagnostic.layer_file() == Some(file)
    })?;
    line_span(text, found.line)?;
    let row = usize::try_from(found.line - 1).ok()?;
    Some((row, error_span(text, found.line, found.column)))
}

/// A diagnostic's location as the compiler messages card links it, `file:line:column` or
/// `file:line`; `None` for one with no file.
#[must_use]
pub fn location(diagnostic: &Diagnostic) -> Option<String> {
    let file = diagnostic.file.as_deref()?;
    Some(match diagnostic.column {
        Some(column) => format!("{file}:{}:{column}", diagnostic.line),
        None => format!("{file}:{}", diagnostic.line),
    })
}

/// The rest of a diagnostic's line after its [`location`]: `: error: message`, or `error:
/// message` for one with no location.
#[must_use]
pub fn after_location(diagnostic: &Diagnostic) -> String {
    let separator = if diagnostic.file.is_some() { ": " } else { "" };
    format!(
        "{separator}{}: {}",
        diagnostic.severity.name(),
        diagnostic.message
    )
}

/// The words beside Compile now for the layer's `state` (Code mockup's "Error: the last good
/// version is still drawing"): what the state means for the frame. The compiler's own words are
/// the messages card's, which `diag/<id>.txt` fills for every failure, so they are not said twice.
#[must_use]
pub fn action_state(state: &LayerState) -> String {
    match state {
        LayerState::Pending => "Compiling".to_owned(),
        LayerState::Live { .. } | LayerState::Carrier { .. } => state.label(),
        LayerState::Error { running: true, .. } => {
            "Error: the last good version is still drawing".to_owned()
        }
        LayerState::Error { running: false, .. } => {
            "Error: no version of this layer is drawing".to_owned()
        }
        LayerState::NoCompiler { why } => format!("No compiler: {why}"),
    }
}

/// `path` with the game's folder `game` written `<Game>`, as the canvas shows a layer's Windows
/// path; `path` itself when it is not under `game`.
#[must_use]
pub fn game_relative(path: &str, game: &str) -> String {
    let game = game.trim_end_matches(['\\', '/']);
    match path.strip_prefix(game) {
        Some(rest) if !game.is_empty() && rest.starts_with(['\\', '/']) => {
            format!("<Game>{rest}")
        }
        _ => path.to_owned(),
    }
}

/// `path` as it fits where `fits` says: whole when it does, else its first component, `ellipsis`
/// and as many of its last components as fit -- `/.../ELDEN RING/Game/...` -- and its last
/// component alone after the ellipsis when nothing longer fits.
#[must_use]
pub fn elide_front(path: &str, ellipsis: &str, fits: impl Fn(&str) -> bool) -> String {
    if fits(path) {
        return path.to_owned();
    }
    let separator = if path.contains('\\') { '\\' } else { '/' };
    let parts: Vec<&str> = path.split(separator).collect();
    let mut shortest = None;
    for skip in 2..parts.len() {
        let tail = parts[skip..].join(&separator.to_string());
        let candidate = format!("{}{separator}{ellipsis}{separator}{tail}", parts[0]);
        if fits(&candidate) {
            return candidate;
        }
        shortest = Some(candidate);
    }
    shortest.unwrap_or_else(|| path.to_owned())
}

/// `text` broken into lines no wider than `max` by `width`, at spaces, as CSS wraps a paragraph:
/// each line's bytes, without the space it broke at. A word wider than `max` is broken where it
/// overflows, after one character at least. An empty text is one empty line.
#[must_use]
pub fn wrap(text: &str, max: f32, width: impl Fn(&str) -> f32) -> Vec<Range<usize>> {
    let mut lines = Vec::new();
    let mut start = 0;
    while start < text.len() {
        let rest = &text[start..];
        if width(rest) <= max {
            lines.push(start..text.len());
            break;
        }
        let mut space = None;
        for (offset, c) in rest.char_indices() {
            if c != ' ' || offset == 0 {
                continue;
            }
            if width(&rest[..offset]) > max {
                break;
            }
            space = Some(offset);
        }
        match space {
            Some(offset) => {
                lines.push(start..start + offset);
                start += offset + 1;
            }
            None => {
                let mut end = rest.chars().next().map_or(rest.len(), char::len_utf8);
                for (offset, _) in rest.char_indices().skip(1) {
                    if width(&rest[..offset]) > max {
                        break;
                    }
                    end = offset;
                }
                lines.push(start..start + end);
                start += end;
            }
        }
    }
    if lines.is_empty() {
        lines.push(0..0);
    }
    lines
}

/// The lines of `count`, each `height` tall from the first's top at `top`, that show between
/// `from` and `to`.
#[must_use]
pub fn visible_rows(top: f32, height: f32, count: usize, from: f32, to: f32) -> Range<usize> {
    if count == 0 || height.is_nan() || height <= 0.0 {
        return 0..0;
    }
    // `as` saturates: a row above the first, or one that is not a number, is 0.
    let first = (((from - top) / height).floor() as usize).min(count);
    let last = (((to - top) / height).ceil() as usize).min(count);
    first..last.max(first)
}

/// The sideways scroll that shows the cursor at `x` (from the text's left edge) in a view
/// `visible` wide scrolled to `scroll`: unchanged while it shows, else moved `step` past it, as
/// Dear ImGui scrolls a field in chunks of a quarter of its width.
#[must_use]
pub fn follow(scroll: f32, x: f32, visible: f32, step: f32) -> f32 {
    if x < scroll {
        (x - step).max(0.0).floor()
    } else if x - visible >= scroll {
        (x - visible + step).floor()
    } else {
        scroll
    }
}

/// Whether the cursor shows `elapsed` seconds after it last moved, as Dear ImGui blinks it.
#[must_use]
pub fn blink_on(elapsed: f64, blinking: bool) -> bool {
    !blinking || elapsed <= 0.0 || elapsed % BLINK_PERIOD <= BLINK_ON
}

/// Whether the completion popup shows for a word starting at `anchor` (`[x, the top of its
/// line]`), its line `line` tall, in the code area `area` (`[x0, y0, x1, y1]`): only while the
/// whole line and the word's start are in view.
#[must_use]
pub fn popup_shows(anchor: [f32; 2], line: f32, area: [f32; 4]) -> bool {
    let fits = anchor[1] >= area[1] - 0.5 && anchor[1] + line <= area[3] + 0.5;
    fits && anchor[0] >= area[0] && anchor[0] <= area[2]
}

/// The completion popup's top-left, `size` big, for a word whose line's top-left is `anchor` and
/// whose line is `line` tall: under the line; over it when under it would pass the code area's
/// bottom (`area` as in [`popup_shows`]) and over it fits; on the side with more room when neither
/// does. Then kept on the `display`.
#[must_use]
pub fn popup_origin(
    anchor: [f32; 2],
    line: f32,
    size: [f32; 2],
    area: [f32; 4],
    display: [f32; 2],
) -> [f32; 2] {
    let below = anchor[1] + line;
    let above = anchor[1] - size[1];
    let fits_below = below + size[1] <= area[3];
    let fits_above = above >= area[1];
    let room_below = area[3] - below;
    let room_above = anchor[1] - area[1];
    let y = if fits_below || (!fits_above && room_below >= room_above) {
        below
    } else {
        above
    };
    [
        anchor[0].min(display[0] - size[0]).max(0.0),
        y.min(display[1] - size[1]).max(0.0),
    ]
}

#[cfg(windows)]
pub(super) use process::CodeState;

/// The editor's state and drawing.
#[cfg(windows)]
mod process {
    use std::ffi::CStr;
    use std::marker::PhantomData;
    use std::ops::Range;
    use std::path::Path;
    use std::sync::atomic::Ordering;
    use std::time::Instant;

    use hudhook::imgui::{
        ImColor32, InputTextCallbackHandler, InputTextMultilineCallback, Key, MouseCursor,
        TextCallbackData, Ui, WindowFlags, WindowHoveredFlags, sys,
    };

    use super::super::files::{self, Job, Reply, Written};
    use super::super::layers_tab::{Light, light};
    use super::super::reference_panel::{self, Action, ReferenceState};
    use super::super::theme::{self, ButtonKind, ButtonSize, CardStyle, Font};
    use super::super::{
        COMPLETION_POPUPS, COMPLETIONS_ACCEPTED, EDIT_CALLBACKS, PASTES, REFERENCE_INSERTS,
        REFERENCE_OPENS,
    };
    use super::{
        ACTION_GAP, DIAGNOSTIC_LINES, GUTTER_PAD_RIGHT, LINK_UNDERLINE, MESSAGE_GAP,
        MIN_EDITOR_LINES, NOTE_INDENT, PATH_GAP, POLL_PERIOD, SAVE_DELAY, STATUS_GAP, Syntax,
        WAVE_DROP, action_state, after_location, blink_on, elide_front, first_error, follow,
        game_relative, highlight, jump_target, line_ranges, location, popup_origin, popup_shows,
        to_editor, to_file, visible_rows, wrap,
    };
    use crate::editor::complete::{self, Insertion, Item, Session, Trigger};
    use crate::editor::diagnostics::{self, Diagnostic, Severity};
    use crate::editor::project::{self, Layer, Project};
    use crate::editor::reference;
    use crate::editor::worker::{self, LayerState, Stamp};

    /// The text field's label, and the same as a C string for the second begin of its window.
    const FIELD: &str = "##code";
    const FIELD_C: &CStr = c"##code";

    /// What Compile now says under the mouse.
    const COMPILE_TIP: &str = "Writes the file now and has the worker read every layer again.";

    /// The button that opens and closes the reference column.
    const REFERENCE_LABEL: &str = "Reference (F1)";

    /// Whether the open file can be edited.
    enum Load {
        Reading,
        Ready,
        /// Why not: not UTF-8, a NUL in it, unreadable.
        Refused(String),
    }

    /// Where the field's cursor was, and what it had selected, in bytes of the text.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    struct Caret {
        at: usize,
        selection: (usize, usize),
    }

    impl Caret {
        fn of(data: &TextCallbackData) -> Self {
            let selection = data.selection();
            Self {
                at: data.cursor_pos(),
                selection: (selection.start, selection.end),
            }
        }
    }

    /// Put the field's cursor and selection at `caret`, when every one of its offsets lands on
    /// the text's characters; else leave them.
    fn put_caret(data: &mut TextCallbackData, caret: Caret) {
        let text = data.str();
        let lands = |at: usize| at <= text.len() && text.is_char_boundary(at);
        if !(lands(caret.at) && lands(caret.selection.0) && lands(caret.selection.1)) {
            return;
        }
        data.set_cursor_pos(caret.at);
        *data.selection_start_mut() = i32::try_from(caret.selection.0).unwrap_or(i32::MAX);
        *data.selection_end_mut() = i32::try_from(caret.selection.1).unwrap_or(i32::MAX);
    }

    /// Make `insertion`'s edits in the field's text, in the order it gives them, and put the
    /// cursor after them. `false`, with nothing changed, when an edit would not land on the text's
    /// characters: Dear ImGui's insert would panic there, inside a callback that cannot unwind.
    fn apply_insertion(data: &mut TextCallbackData, insertion: &Insertion) -> bool {
        let text = data.str();
        let lands = |at: usize| at <= text.len() && text.is_char_boundary(at);
        let fits = insertion.edits.iter().all(|edit| {
            edit.range.start <= edit.range.end && lands(edit.range.start) && lands(edit.range.end)
        });
        if !fits {
            return false;
        }
        // Each edit's range is in the text as it was; in falling order, no edit moves the next.
        let removed: Vec<usize> = insertion
            .edits
            .iter()
            .map(|edit| edit.removed_chars(text))
            .collect();
        for (edit, removed) in insertion.edits.iter().zip(removed) {
            if removed > 0 {
                data.remove_chars(edit.range.start, removed);
            }
            if !edit.text.is_empty() {
                data.insert_chars(edit.range.start, &edit.text);
            }
        }
        let at = insertion.cursor;
        put_caret(
            data,
            Caret {
                at,
                selection: (at, at),
            },
        );
        true
    }

    /// What this frame's keys ask of the completion popup: read before the field is drawn and
    /// done in its callbacks, which run after the field has handled the keys.
    #[derive(Default)]
    struct Keys {
        /// Tab or Enter accepts the selected item.
        accept: bool,
        /// Tabs or line breaks the field must not type: the ones the accepting key brings.
        swallow: u32,
        /// Up or Down moved the selection, so the cursor goes back to where it was.
        hold: Option<Caret>,
        /// Ctrl+Space opens the popup whatever was typed.
        explicit: bool,
    }

    /// The text field's callbacks: the edit count, the cursor and selection copied out for the
    /// panel to draw, a cursor move asked for by a click on a message, which applies once the
    /// field has the keyboard, and the completion popup's keys and its following of the text.
    struct Callbacks<'a> {
        jump: &'a mut Option<usize>,
        caret: &'a mut Option<Caret>,
        completion: &'a mut Option<Session>,
        keys: &'a mut Keys,
        /// The text's length and the cursor before this frame, to tell one typed character from
        /// a paste or a deletion.
        before: (usize, Option<usize>),
        /// Characters the filter let through this frame, and the last of them.
        typed: u32,
        last_typed: Option<char>,
        /// The name of an item written this frame, for the log.
        accepted: &'a mut Option<String>,
        /// An entry the reference column was clicked on, to write at the cursor once the jump
        /// has put the cursor back; and its name once written, for the log.
        insert: &'a mut Option<&'static Item>,
        inserted: &'a mut Option<String>,
    }

    impl Callbacks<'_> {
        /// Every callback that sees the text: the jump, the cursor held by Up or Down, an entry
        /// from the reference, an accept, the popup following the text or opening, and the cursor
        /// copied out.
        fn observe(&mut self, data: &mut TextCallbackData, edited: bool) {
            if let Some(at) = self.jump.take() {
                put_caret(
                    data,
                    Caret {
                        at,
                        selection: (at, at),
                    },
                );
            }
            if let Some(hold) = self.keys.hold.take() {
                put_caret(data, hold);
            }
            if let Some(item) = self.insert.take() {
                self.write(data, item);
            }
            if std::mem::take(&mut self.keys.accept) {
                self.accept(data);
            } else {
                self.follow(data, edited);
            }
            *self.caret = Some(Caret::of(data));
        }

        /// Write the selected item, and open the popup again where it asks for one.
        fn accept(&mut self, data: &mut TextCallbackData) {
            let item = self
                .completion
                .take()
                .and_then(|session| session.selected_item().cloned());
            let Some(item) = item else {
                return;
            };
            let Some(insertion) = complete::accept(data.str(), data.cursor_pos(), &item) else {
                return;
            };
            if !apply_insertion(data, &insertion) {
                return;
            }
            COMPLETIONS_ACCEPTED.fetch_add(1, Ordering::Relaxed);
            *self.accepted = Some(item.name);
            if insertion.reopen {
                self.open(data, Trigger::Explicit);
            }
        }

        /// Write an entry of the reference column at the cursor, as accepting a completion
        /// writes one: the same edits, made the same way.
        fn write(&mut self, data: &mut TextCallbackData, item: &'static Item) {
            let Some(insertion) = reference::insertion(data.str(), data.cursor_pos(), item) else {
                return;
            };
            if apply_insertion(data, &insertion) {
                REFERENCE_INSERTS.fetch_add(1, Ordering::Relaxed);
                *self.inserted = Some(item.name.clone());
                *self.completion = None;
            }
        }

        /// Keep the popup on the word it opened for, or open one for what was just typed or for
        /// Ctrl+Space.
        fn follow(&mut self, data: &TextCallbackData, edited: bool) {
            let (text, cursor) = (data.str(), data.cursor_pos());
            if let Some(session) = self.completion.as_mut()
                && session.update(text, cursor)
            {
                return;
            }
            *self.completion = None;
            if std::mem::take(&mut self.keys.explicit) {
                self.open(data, Trigger::Explicit);
            } else if edited && let Some(typed) = self.typed_char(text, cursor) {
                self.open(data, Trigger::Typed(typed));
            }
        }

        fn open(&mut self, data: &TextCallbackData, trigger: Trigger) {
            *self.completion = Session::open(data.str(), data.cursor_pos(), trigger);
            if self.completion.is_some() {
                COMPLETION_POPUPS.fetch_add(1, Ordering::Relaxed);
            }
        }

        /// The one character typed this frame: it alone passed the filter, the text grew by it
        /// and the cursor moved past it.
        fn typed_char(&self, text: &str, cursor: usize) -> Option<char> {
            let typed = self.last_typed.filter(|_| self.typed == 1)?;
            let (length, before) = self.before;
            let grew = text.len() == length + typed.len_utf8();
            let moved = before.is_some_and(|at| cursor == at + typed.len_utf8());
            (grew && moved).then_some(typed)
        }
    }

    impl InputTextCallbackHandler for Callbacks<'_> {
        fn char_filter(&mut self, c: char) -> Option<char> {
            if self.keys.swallow > 0 && matches!(c, '\t' | '\n') {
                self.keys.swallow -= 1;
                return None;
            }
            self.typed = self.typed.saturating_add(1);
            self.last_typed = Some(c);
            Some(c)
        }

        fn on_edit(&mut self, mut data: TextCallbackData) {
            EDIT_CALLBACKS.fetch_add(1, Ordering::Relaxed);
            self.observe(&mut data, true);
        }

        fn on_always(&mut self, mut data: TextCallbackData) {
            self.observe(&mut data, false);
        }
    }

    /// The text split into lines and coloured, kept until the text changes.
    struct Layout {
        version: u64,
        lines: Vec<Range<usize>>,
        runs: Vec<Vec<(Range<usize>, Syntax)>>,
        /// The widest line, and the font and size it was measured in.
        widest: Option<(usize, u32, f32)>,
    }

    impl Layout {
        fn new(text: &str, version: u64) -> Self {
            Self {
                version,
                lines: line_ranges(text),
                runs: highlight(text),
                widest: None,
            }
        }

        /// The widest line's width in `metrics`.
        fn widest(&mut self, text: &str, metrics: &Metrics) -> f32 {
            let key = (metrics.font as usize, metrics.size.to_bits());
            match self.widest {
                Some((font, size, width)) if (font, size) == key => width,
                _ => {
                    let width = self
                        .lines
                        .iter()
                        .filter_map(|line| text.get(line.clone()))
                        .map(|line| metrics.width(line))
                        .fold(0.0, f32::max);
                    self.widest = Some((key.0, key.1, width));
                    width
                }
            }
        }

        /// The line holding byte `at`, the end of a line counting as on it.
        fn row_of(&self, at: usize) -> usize {
            self.lines
                .partition_point(|line| line.end < at)
                .min(self.lines.len().saturating_sub(1))
        }
    }

    /// The code's font and the code area's sizes this frame, in screen pixels.
    struct Metrics {
        /// The font the code is set in: the panel's mono font, or the host's default.
        font: *mut sys::ImFont,
        /// Its size in the code area, whatever the window it is drawn in.
        size: f32,
        /// A line's height, the gap above a line's glyphs, and a glyph's baseline under its top.
        line: f32,
        lead: f32,
        baseline: f32,
        /// The text's padding inside the area (horizontal, vertical).
        pad: [f32; 2],
        /// The font's own size in the field's copy: lines [`Metrics::line`] apart.
        stretched: f32,
        /// A space's width, for the sliver a selected line break shows.
        space: f32,
    }

    impl Metrics {
        fn new(ui: &Ui) -> Option<Self> {
            let t = theme::current();
            let font = match t.font_ptr(Font::Mono) {
                Some(font) => font,
                // SAFETY: a frame is open (we hold its `&Ui`), so there is a current font.
                None => unsafe { sys::igGetFont() },
            };
            if font.is_null() {
                return None;
            }
            // SAFETY: an atlas font, live for the frame; atlas fonts are never freed.
            let (own, font_scale, ascent) =
                unsafe { ((*font).FontSize, (*font).Scale, (*font).Ascent) };
            // The code area's font scale: the panel window's, which its text is the size of
            // (`theme::apply_font_scale`).
            let window_scale = t.font_scale;
            let global = theme::global_font_scale(ui);
            // Dear ImGui's size for a window this font is pushed in, one level under a window
            // scaled `window_scale` (`SetCurrentFont`, `CalcFontSize`).
            let size = (global * own * font_scale).max(1.0) * window_scale;
            let line = t.px(theme::CODE_LINE_HEIGHT).round().max(1.0);
            let per_own = global * font_scale * window_scale;
            let stretched = if per_own.is_finite() && per_own > 0.0 && own > 0.0 {
                line / per_own
            } else {
                own
            };
            let mut metrics = Self {
                font,
                size,
                line,
                lead: ((line - size) * 0.5).round().max(0.0),
                baseline: if own > 0.0 { ascent * size / own } else { size },
                pad: theme::CODE_PAD.map(|pad| t.px(pad).round()),
                stretched,
                space: 0.0,
            };
            metrics.space = metrics.width(" ");
            Some(metrics)
        }

        /// `text`'s width in the code font, unrounded, as the field and the draw list add it up.
        fn width(&self, text: &str) -> f32 {
            let mut size = sys::ImVec2::zero();
            let range = text.as_bytes().as_ptr_range();
            // SAFETY: an atlas font, live for the frame; the text is given by its two ends.
            unsafe {
                sys::ImFont_CalcTextSizeA(
                    &mut size,
                    self.font,
                    self.size,
                    f32::MAX,
                    0.0,
                    range.start.cast(),
                    range.end.cast(),
                    std::ptr::null_mut(),
                );
            }
            size.x
        }
    }

    /// The code font on the current window's draw list, at the code area's size whatever the
    /// window's font scale.
    struct Ink<'a> {
        list: *mut sys::ImDrawList,
        metrics: &'a Metrics,
    }

    impl<'a> Ink<'a> {
        fn here(_ui: &Ui, metrics: &'a Metrics) -> Self {
            Self {
                // SAFETY: a frame is open, so a window is current and has a draw list.
                list: unsafe { sys::igGetWindowDrawList() },
                metrics,
            }
        }

        /// `text` laid out from `at`, drawn only inside `clip` (`[x0, y0, x1, y1]`). Drawing a
        /// run of a line as the line up to the run's end, clipped to the run, puts each glyph
        /// where the line puts it, which is where the field measures it.
        fn text(&self, at: [f32; 2], colour: [f32; 4], text: &str, clip: [f32; 4]) {
            if clip[2] <= clip[0] || clip[3] <= clip[1] {
                return;
            }
            let range = text.as_bytes().as_ptr_range();
            let clip = sys::ImVec4 {
                x: clip[0],
                y: clip[1],
                z: clip[2],
                w: clip[3],
            };
            // SAFETY: the window's draw list and an atlas font, live for the frame; the text is
            // given by its two ends.
            unsafe {
                sys::ImDrawList_AddText_FontPtr(
                    self.list,
                    self.metrics.font,
                    self.metrics.size,
                    sys::ImVec2::new(at[0], at[1]),
                    packed(colour),
                    range.start.cast(),
                    range.end.cast(),
                    0.0,
                    &clip,
                );
            }
        }

        /// A rectangle rounded on its left corners only: the gutter inside the rounded area.
        fn fill_left(&self, min: [f32; 2], max: [f32; 2], colour: [f32; 4], radius: f32) {
            // SAFETY: as in `text`.
            unsafe {
                sys::ImDrawList_AddRectFilled(
                    self.list,
                    sys::ImVec2::new(min[0].round(), min[1].round()),
                    sys::ImVec2::new(max[0].round(), max[1].round()),
                    packed(colour),
                    radius,
                    sys::ImDrawFlags_RoundCornersLeft as i32,
                );
            }
        }
    }

    fn packed(colour: [f32; 4]) -> u32 {
        ImColor32::from(colour).to_bits()
    }

    fn round2(point: [f32; 2]) -> [f32; 2] {
        point.map(f32::round)
    }

    /// The field's font pushed until dropped: a copy of the code font, its own size stretched to
    /// [`Metrics::stretched`]. Dear ImGui reads the copy until it is popped and writes nothing to
    /// a font while it draws, so the copy outliving the push is all that is needed.
    struct StretchedFont<'a> {
        _copy: PhantomData<&'a mut sys::ImFont>,
    }

    impl<'a> StretchedFont<'a> {
        fn push(_ui: &Ui, copy: &'a mut sys::ImFont) -> Self {
            // SAFETY: a frame is open; the copy shares the atlas's glyph tables, which live for
            // the frame, and the borrow keeps it alive until the pop.
            unsafe { sys::igPushFont(copy) };
            Self { _copy: PhantomData }
        }
    }

    impl Drop for StretchedFont<'_> {
        fn drop(&mut self) {
            // SAFETY: pops exactly the push above, in the same window.
            unsafe { sys::igPopFont() };
        }
    }

    /// Where the code went this frame, for the gutter beside it.
    struct Rows {
        /// The first line's top, the lines shown, and the line of the first error.
        top: f32,
        shown: Range<usize>,
        error: Option<usize>,
    }

    /// The action row's words and where they go.
    struct Actions {
        state: String,
        state_colour: [f32; 4],
        note: String,
        note_colour: [f32; 4],
        /// The state fits on the button's line; else how tall it is wrapped.
        state_wraps: Option<f32>,
        /// The save note shares the button's line.
        note_inline: bool,
        height: f32,
    }

    /// One message in the card, wrapped.
    struct Message {
        /// The location and the rest of the line, as one text, and where the location ends.
        text: String,
        split: usize,
        location_colour: [f32; 4],
        rest_colour: [f32; 4],
        /// How far it is set in, at 1080p.
        indent: f32,
        lines: Vec<Range<usize>>,
        /// The line and column of this file a click puts the cursor on.
        jump: Option<(u32, Option<u32>)>,
    }

    /// The compiler messages card, measured.
    struct Messages {
        rows: Vec<Message>,
        line: f32,
        /// Its content's height, the part shown, and the card's height.
        content: f32,
        shown: f32,
        height: f32,
    }

    /// The Code sub-tab's state: one layer's file at a time.
    pub(in crate::menu) struct CodeState {
        /// The layer whose file is open, and that file's name.
        open: Option<(u32, String)>,
        text: String,
        /// Changed whenever [`Self::text`] is, so the colouring is made again.
        version: u64,
        /// The text as last read or written: what the file holds as far as the panel knows.
        saved: String,
        crlf: bool,
        stamp: Option<Stamp>,
        load: Load,
        /// When the text last changed, while it is unsaved.
        dirty_since: Option<Instant>,
        polled: Option<Instant>,
        windows_path: String,
        /// The Windows path with the game's folder written `<Game>`.
        windows_shown: String,
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
        layout: Option<Layout>,
        /// The field's cursor and selection as its callbacks last reported them, and as the panel
        /// last drew them, and when they last moved (Dear ImGui's clock).
        caret: Option<Caret>,
        drawn_caret: Option<Caret>,
        caret_since: f64,
        /// The field had the keyboard this frame.
        active: bool,
        /// The field's own scroll, read back each frame, and the panel's sideways scroll.
        scroll_y: f32,
        scroll_x: f32,
        /// The completion popup, while it is open.
        completion: Option<Session>,
        /// The reference column beside the code, and an entry clicked in it that the field
        /// writes once it has the keyboard back.
        reference: ReferenceState,
        pending: Option<&'static Item>,
    }

    impl CodeState {
        pub(in crate::menu) fn new() -> Self {
            Self {
                open: None,
                text: String::new(),
                version: 0,
                saved: String::new(),
                crlf: false,
                stamp: None,
                load: Load::Reading,
                dirty_since: None,
                polled: None,
                windows_path: String::new(),
                windows_shown: String::new(),
                unix_path: None,
                saving: false,
                save_error: None,
                diagnostics: Vec::new(),
                other_lines: Vec::new(),
                diag_for: None,
                diag_polled: None,
                jump: None,
                focus: false,
                layout: None,
                caret: None,
                drawn_caret: None,
                caret_since: 0.0,
                active: false,
                scroll_y: 0.0,
                scroll_x: 0.0,
                completion: None,
                reference: ReferenceState::new(),
                pending: None,
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
            // The reference column stays as it was, open or not, from one layer to the next.
            let kept = std::mem::replace(&mut self.reference, ReferenceState::new());
            *self = Self::new();
            self.reference = kept;
            // The completion vocabulary, and the reference built from it, are read from the
            // shader sources on their first use; here, rather than inside the field's callback.
            let _ = reference::entries();
            let path = dir.join(project::LAYERS_DIRECTORY).join(&layer.file);
            self.windows_path = path.display().to_string();
            self.windows_shown = match dir.parent() {
                Some(game) => game_relative(&self.windows_path, &game.display().to_string()),
                None => self.windows_path.clone(),
            };
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
                                self.version += 1;
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

        /// The sub-tab, as `Code.dc.html` lays it out 10 apart: the paths, the code area taking
        /// the height the rest leaves, the action row, and the compiler messages.
        pub(in crate::menu) fn draw(
            &mut self,
            ui: &Ui,
            project: &Project,
            layer: &Layer,
            state: Option<&LayerState>,
            dir: &Path,
        ) {
            self.open(layer, dir);
            let t = theme::current();
            let gap = t.px(theme::CONTENT_GAP).round();
            let _spacing = theme::spacing(ui, theme::CONTENT_GAP);
            self.reference_keys(ui);
            self.draw_paths(ui);
            let width = ui.content_region_avail()[0].floor().max(1.0);
            let (lamp, _) = light(project, layer, state);
            let actions = self.measure_actions(ui, state, width);
            let messages = self.measure_messages(ui, &layer.file, lamp, width);
            if let Some(metrics) = Metrics::new(ui) {
                let least = MIN_EDITOR_LINES * metrics.line + 2.0 * (metrics.pad[1] + t.stroke());
                let height =
                    (ui.content_region_avail()[1] - actions.height - messages.height - 2.0 * gap)
                        .floor()
                        .max(least.ceil());
                // With the reference open, the code area gives it a column at its right.
                let at = round2(ui.cursor_screen_pos());
                let column = if self.reference.open {
                    reference_panel::column_width(width, t.scale)
                } else {
                    0.0
                };
                let code_width = if column > 0.0 {
                    (width - column - gap).max(1.0)
                } else {
                    width
                };
                self.draw_area(ui, &layer.file, [code_width, height], &metrics);
                if self.reference.open {
                    ui.set_cursor_screen_pos([at[0] + code_width + gap, at[1]]);
                    let action = self.reference.draw(ui, [column, height], layer.stage);
                    self.act(ui, action);
                    ui.set_cursor_screen_pos(at);
                    ui.dummy([width, height]);
                }
            }
            self.draw_actions(ui, dir, lamp, &actions, width);
            self.draw_messages(ui, &messages, width);
        }

        /// The keyboard back in the code, its cursor where it was.
        fn return_to_code(&mut self) {
            self.jump = self.caret.map(|caret| caret.at);
            self.focus = true;
        }

        /// [`reference_panel::KEY`]: open the reference with its search field taking the keyboard,
        /// or from the search field give the keyboard back to the code. Escape while neither the
        /// code nor the search has the keyboard closes the reference.
        fn reference_keys(&mut self, ui: &Ui) {
            let io = ui.io();
            let plain = !(io.key_ctrl || io.key_shift || io.key_alt);
            if plain && ui.is_key_pressed_no_repeat(Key::F1) {
                if !self.reference.open {
                    self.open_reference();
                } else if self.reference.searching() {
                    self.return_to_code();
                } else {
                    self.reference.open_search();
                }
            } else if self.reference.open
                && !self.active
                && !self.reference.searching()
                && ui.is_key_pressed(Key::Escape)
            {
                self.reference.close();
            }
        }

        fn open_reference(&mut self) {
            self.reference.open_search();
            if REFERENCE_OPENS.fetch_add(1, Ordering::Relaxed) == 0 {
                crate::log(format_args!(
                    "code reference: opened for the first time this session, {} entries",
                    reference::entries().len()
                ));
            }
        }

        /// What the reference column asked for this frame.
        fn act(&mut self, ui: &Ui, action: Action) {
            match action {
                Action::None => {}
                Action::Write(item) => {
                    // The field takes the keyboard back on the next frame, the cursor goes back to
                    // where it was, and its callback writes the entry there.
                    self.pending = Some(item);
                    self.return_to_code();
                }
                Action::Copy(item) => {
                    ui.set_clipboard_text(&item.name);
                    crate::log(format_args!(
                        "code reference: copied the mask term `{}` for the Selector tab",
                        item.name
                    ));
                }
                Action::Close => {
                    self.reference.close();
                    self.return_to_code();
                }
            }
        }

        /// Both paths in quiet 11-pixel mono, 2 apart, each shortened from the front to fit.
        fn draw_paths(&self, ui: &Ui) {
            let t = theme::current();
            let at = round2(ui.cursor_screen_pos());
            let width = ui.content_region_avail()[0].max(1.0);
            let ellipsis = if t.custom_fonts() { "\u{2026}" } else { "..." };
            theme::with_font(ui, Font::MonoSmall, || {
                let line = ui.text_line_height();
                let fits = |path: &str| ui.calc_text_size(path)[0] <= width;
                let paint = theme::painter(ui);
                let paths = [Some(&self.windows_shown), self.unix_path.as_ref()];
                let mut y = at[1];
                for (index, path) in paths.into_iter().flatten().enumerate() {
                    if index > 0 {
                        y += line + t.px(PATH_GAP).round();
                    }
                    paint.text(
                        [at[0], y],
                        theme::TEXT_QUIET,
                        &elide_front(path, ellipsis, fits),
                    );
                }
                ui.dummy([width, (y + line - at[1]).max(1.0)]);
            });
        }

        /// The code area at the cursor, `size` in screen pixels: `#0B0C0F` behind a 1-pixel
        /// border, rounded 3, with the gutter on its left and the text, or why there is none.
        fn draw_area(&mut self, ui: &Ui, file: &str, size: [f32; 2], metrics: &Metrics) {
            let t = theme::current();
            let stroke = t.stroke();
            let radius = t.px(theme::FRAME_RADIUS);
            let at = round2(ui.cursor_screen_pos());
            let max = [at[0] + size[0], at[1] + size[1]];
            theme::register_rect("code_area", [at[0], at[1], size[0], size[1]]);
            // CSS sizes the gutter's content 34 wide, then its right padding, then its rule.
            let numbers_right = at[0] + stroke + t.px(theme::GUTTER_WIDTH).round();
            let rule = numbers_right + t.px(GUTTER_PAD_RIGHT).round();
            let view = [
                [rule + stroke, at[1] + stroke],
                [max[0] - stroke, max[1] - stroke],
            ];
            let paint = theme::painter(ui);
            paint.fill(at, max, theme::CODE_BG, radius);
            Ink::here(ui, metrics).fill_left(
                [at[0] + stroke, at[1] + stroke],
                [rule, max[1] - stroke],
                theme::GUTTER_BG,
                (radius - stroke).max(0.0),
            );
            paint.vline(rule, at[1] + stroke, max[1] - stroke, theme::GUTTER_RULE);
            paint.outline(at, max, theme::RULE, radius);

            let why_not = match &self.load {
                Load::Ready => None,
                Load::Reading => Some((theme::TEXT_QUIET, "Reading the file...".to_owned())),
                Load::Refused(why) => Some((theme::ERROR_TEXT, why.clone())),
            };
            match why_not {
                None => {
                    let rows = self.draw_view(ui, file, view, metrics);
                    let gutter = [at[0] + stroke, view[0][1], rule, view[1][1]];
                    draw_numbers(ui, metrics, &rows, numbers_right, gutter);
                }
                Some((colour, words)) => say_in_area(ui, view, metrics, colour, &words),
            }
            ui.set_cursor_screen_pos(at);
            ui.dummy(size);
        }

        /// The text, typed into the invisible field and drawn under it, in a child window over
        /// `view` (`[min, max]`). Returns where its lines went.
        fn draw_view(&mut self, ui: &Ui, file: &str, view: [[f32; 2]; 2], m: &Metrics) -> Rows {
            let [min, max] = view;
            let size = [(max[0] - min[0]).max(1.0), (max[1] - min[1]).max(1.0)];
            let mut rows = Rows {
                top: min[1] + m.pad[1] - self.scroll_y,
                shown: 0..0,
                error: None,
            };
            ui.set_cursor_screen_pos(min);
            let Some(_view) = ui
                .child_window("##code-view")
                .size(size)
                .flags(WindowFlags::NO_SCROLLBAR | WindowFlags::NO_SCROLL_WITH_MOUSE)
                .begin()
            else {
                return rows;
            };
            // The panel window's font scale, which the code's metrics were worked out at.
            theme::apply_font_scale(ui);
            self.scroll_sideways(ui, m, size[0]);
            let edited = self.draw_field(ui, min, size, m);

            let mut layout = self
                .layout
                .take()
                .filter(|layout| layout.version == self.version)
                .unwrap_or_else(|| Layout::new(&self.text, self.version));
            let caret = self.caret.filter(|_| self.active);
            let now = ui.time();
            if caret != self.drawn_caret || edited {
                self.caret_since = now;
            }
            let moved = caret.is_some() && (caret != self.drawn_caret || edited);
            self.drawn_caret = caret;

            // The field's scrollbar, when it has one, covers the text's right edge.
            let content = layout.lines.len() as f32 * m.line + 2.0 * m.pad[1];
            let bar = if content > size[1] {
                theme::px(theme::SCROLLBAR_SIZE).round()
            } else {
                0.0
            };
            let text_width = (size[0] - bar).max(1.0);
            let caret_x = caret.and_then(|caret| {
                let line = layout.lines.get(layout.row_of(caret.at))?;
                Some(m.width(self.text.get(line.start..caret.at)?))
            });
            if moved && let Some(x) = caret_x {
                let visible = (text_width - 2.0 * m.pad[0]).max(1.0);
                self.scroll_x = follow(self.scroll_x, x, visible, (text_width * 0.25).floor());
            }
            let widest = layout.widest(&self.text, m);
            let most = (widest + 2.0 * m.pad[0] + m.space - text_width).max(0.0);
            self.scroll_x = self.scroll_x.clamp(0.0, most.ceil()).round();

            let right = max[0] - bar;
            rows.top = min[1] + m.pad[1] - self.scroll_y;
            rows.shown = visible_rows(rows.top, m.line, layout.lines.len(), min[1], max[1]);
            let error = first_error(&self.diagnostics, file, &self.text);
            rows.error = error.as_ref().map(|(row, _)| *row);
            let left = min[0] + m.pad[0] - self.scroll_x;
            let page = Page {
                left,
                top: rows.top,
                clip: [min[0], min[1], right, max[1]],
            };
            let paint = theme::painter(ui);
            if let Some(row) = rows.error.filter(|row| rows.shown.contains(row)) {
                let y = page.row_top(row, m);
                paint.fill(
                    [min[0] + m.pad[0], y],
                    [right - m.pad[0], y + m.line],
                    theme::ERROR_LINE_BG,
                    0.0,
                );
            }
            if let Some(caret) = caret {
                self.draw_selection(ui, &layout, &page, &rows.shown, caret, m);
            }
            self.draw_code(ui, &layout, &page, &rows.shown, m);
            if let Some((row, Some(span))) = error.filter(|(row, _)| rows.shown.contains(row))
                && let Some(line) = layout.lines.get(row)
                && let (Some(before), Some(through)) = (
                    self.text.get(line.start..span.start),
                    self.text.get(line.start..span.end),
                )
            {
                let y = page.row_top(row, m) + m.lead + m.baseline + theme::px(WAVE_DROP);
                paint.with_clip([min[0], min[1]], [right, max[1]], || {
                    paint.wave(
                        left + m.width(before),
                        left + m.width(through),
                        y,
                        theme::ERROR_WAVE,
                    );
                });
            }
            let blinking = ui.io().config_input_text_cursor_blink;
            if let (Some(caret), Some(x)) = (caret, caret_x)
                && blink_on(now - self.caret_since, blinking)
            {
                let x = (left + x).floor();
                let y = page.row_top(layout.row_of(caret.at), m) + m.lead;
                if x >= min[0] && x < right {
                    paint.vline(x, y, y + m.size, theme::TEXT);
                }
            }
            if self.active
                && let Some(session) = &self.completion
            {
                let area = [min[0], min[1], right, max[1]];
                draw_completion(ui, &self.text, session, &layout, &page, area, m);
            }
            self.layout = Some(layout);
            rows
        }

        /// The sideways wheel, or Shift with the wheel, over the code area moves the text.
        fn scroll_sideways(&mut self, ui: &Ui, m: &Metrics, width: f32) {
            let io = ui.io();
            let wheel = if io.mouse_wheel_h != 0.0 {
                io.mouse_wheel_h
            } else if io.key_shift {
                io.mouse_wheel
            } else {
                0.0
            };
            if wheel != 0.0 && ui.is_window_hovered_with_flags(WindowHoveredFlags::CHILD_WINDOWS) {
                // Dear ImGui's step for a wheel notch: two lines' worth, at most two thirds across.
                let step = (2.0 * m.size).min(width * 0.67).floor();
                self.scroll_x -= wheel * step;
            }
        }

        /// The keys that drive the completion popup this frame, read before the field sees them:
        /// Ctrl+Space, and with the popup open and no modifier held, Up, Down, Tab and Enter. A
        /// Tab or Enter whose item would change nothing closes the popup and reaches the field.
        fn completion_keys(&mut self, ui: &Ui) -> Keys {
            let mut keys = Keys::default();
            if !self.active {
                return keys;
            }
            let io = ui.io();
            keys.explicit = io.key_ctrl && ui.is_key_pressed(Key::Space);
            let plain = !(io.key_ctrl || io.key_shift || io.key_alt);
            let Some(session) = self.completion.as_mut().filter(|_| plain) else {
                return keys;
            };
            if ui.is_key_pressed(Key::DownArrow) {
                session.select_next();
                keys.hold = self.caret;
            } else if ui.is_key_pressed(Key::UpArrow) {
                session.select_previous();
                keys.hold = self.caret;
            } else if ui.is_key_pressed(Key::Tab)
                || ui.is_key_pressed(Key::Enter)
                || ui.is_key_pressed(Key::KeypadEnter)
            {
                let writes = self
                    .caret
                    .is_some_and(|caret| session.accept(&self.text, caret.at).is_some());
                if writes {
                    keys.accept = true;
                    keys.swallow = 1;
                } else {
                    self.completion = None;
                }
            }
            keys
        }

        /// The text field over the view at `min`, `size`, moved left by the sideways scroll, in
        /// the stretched font with everything it draws clear. Escape skips it for the frame, so
        /// it loses the keyboard without putting its text back. Returns whether the text changed.
        fn draw_field(&mut self, ui: &Ui, min: [f32; 2], size: [f32; 2], m: &Metrics) -> bool {
            let mut keys = self.completion_keys(ui);
            // Escape puts a Dear ImGui text field back to the text it had when it took the
            // keyboard: in a code editor, every edit since the player clicked in, which would then
            // be saved. The field is left out of the frame Escape goes down in, which takes the
            // keyboard from it with no revert; its text is already in `self.text`. With the
            // completion popup open, Escape only closes it: the field takes the keyboard back on
            // the next frame with the cursor where it was, as a click on a message puts it.
            if ui.is_key_pressed(Key::Escape) {
                if self.completion.take().is_some()
                    && let Some(caret) = self.caret
                {
                    self.jump = Some(caret.at);
                    self.focus = true;
                }
                self.active = false;
                return false;
            }
            let field = [size[0] + self.scroll_x, size[1]];
            ui.set_cursor_screen_pos([min[0] - self.scroll_x, min[1]]);
            if self.focus {
                ui.set_keyboard_focus_here();
                self.focus = false;
            }
            // SAFETY: an atlas font, live for the frame; the copy is only read.
            let mut stretched = unsafe { *m.font };
            stretched.FontSize = m.stretched;
            let before = (self.text.len(), self.caret.map(|caret| caret.at));
            let popups = COMPLETION_POPUPS.load(Ordering::Relaxed);
            let mut accepted = None;
            let mut inserted = None;
            let edited = {
                let _look = theme::clear_field(ui, m.pad);
                let _font = StretchedFont::push(ui, &mut stretched);
                ui.input_text_multiline(FIELD, &mut self.text, field)
                    .allow_tab_input(true)
                    .no_horizontal_scroll(true)
                    .callback(
                        InputTextMultilineCallback::ALWAYS
                            | InputTextMultilineCallback::EDIT
                            | InputTextMultilineCallback::CHAR_FILTER,
                        Callbacks {
                            jump: &mut self.jump,
                            caret: &mut self.caret,
                            completion: &mut self.completion,
                            keys: &mut keys,
                            before,
                            typed: 0,
                            last_typed: None,
                            accepted: &mut accepted,
                            insert: &mut self.pending,
                            inserted: &mut inserted,
                        },
                    )
                    .build()
            };
            self.active = ui.is_item_active();
            if !self.active {
                self.completion = None;
            }
            if popups == 0 && COMPLETION_POPUPS.load(Ordering::Relaxed) > 0 {
                crate::log(format_args!(
                    "code completion: the popup opened for the first time this session"
                ));
            }
            if let Some(name) = accepted {
                crate::log(format_args!(
                    "code completion: wrote `{name}` into the layer; {} accepted this session",
                    COMPLETIONS_ACCEPTED.load(Ordering::Relaxed)
                ));
            }
            if let Some(name) = inserted {
                crate::log(format_args!(
                    "code reference: wrote `{name}` into the layer; {} written this session",
                    REFERENCE_INSERTS.load(Ordering::Relaxed)
                ));
            }
            if self.active {
                let io = ui.io();
                let paste = (io.key_ctrl && ui.is_key_pressed_no_repeat(Key::V))
                    || (io.key_shift && ui.is_key_pressed_no_repeat(Key::Insert));
                if paste {
                    PASTES.fetch_add(1, Ordering::Relaxed);
                }
            }
            // The field begins its child window whenever its item is added, and Dear ImGui leaves
            // out only an item clipped away; an item it added is one that shows.
            if ui.is_item_visible() {
                // SAFETY: a frame is open and the window this names was begun this frame, so
                // this begin appends to it and moves nothing; `NavFlattened` skips the
                // navigation step that would take the keyboard from a just-activated field. The
                // size is the one the field gave it, so its size does not change.
                unsafe {
                    sys::igBeginChild_Str(
                        FIELD_C.as_ptr(),
                        sys::ImVec2::new(field[0], field[1]),
                        false,
                        sys::ImGuiWindowFlags_NavFlattened as i32,
                    );
                    self.scroll_y = sys::igGetScrollY();
                    sys::igEndChild();
                }
            }
            if edited {
                self.version += 1;
                if self.text != self.saved {
                    self.dirty_since = Some(Instant::now());
                }
            }
            edited
        }

        /// The selection, gold under the text, row by row; a selected line break shows as half a
        /// space, as Dear ImGui shows it.
        fn draw_selection(
            &self,
            ui: &Ui,
            layout: &Layout,
            page: &Page,
            shown: &Range<usize>,
            caret: Caret,
            m: &Metrics,
        ) {
            let (start, end) = caret.selection;
            if start >= end {
                return;
            }
            let paint = theme::painter(ui);
            let rows = layout.lines.iter().enumerate();
            for (row, line) in rows.take(shown.end).skip(shown.start) {
                if start > line.end || end <= line.start {
                    continue;
                }
                let from = start.max(line.start);
                let to = end.min(line.end);
                let (Some(before), Some(through)) = (
                    self.text.get(line.start..from),
                    self.text.get(line.start..to),
                ) else {
                    continue;
                };
                let x0 = page.left + m.width(before);
                let mut x1 = page.left + m.width(through);
                if x1 <= x0 {
                    x1 = x0 + (m.space * 0.5).floor().max(1.0);
                }
                let y = page.row_top(row, m);
                paint.with_clip(
                    [page.clip[0], page.clip[1]],
                    [page.clip[2], page.clip[3]],
                    || {
                        paint.fill([x0, y], [x1, y + m.line], theme::TEXT_SELECTED_BG, 0.0);
                    },
                );
            }
        }

        /// The shown lines' text, run by run in its syntax colour.
        fn draw_code(
            &self,
            ui: &Ui,
            layout: &Layout,
            page: &Page,
            shown: &Range<usize>,
            m: &Metrics,
        ) {
            let ink = Ink::here(ui, m);
            let rows = layout.lines.iter().zip(&layout.runs).enumerate();
            for (row, (line, runs)) in rows.take(shown.end).skip(shown.start) {
                let Some(text) = self.text.get(line.clone()) else {
                    continue;
                };
                let y = page.row_top(row, m) + m.lead;
                for (run, kind) in runs {
                    let (Some(before), Some(through)) = (
                        text.get(..run.start - line.start),
                        text.get(..run.end - line.start),
                    ) else {
                        continue;
                    };
                    let x0 = page.left + m.width(before);
                    let x1 = page.left + m.width(through);
                    if x1 < page.clip[0] || x0 > page.clip[2] {
                        continue;
                    }
                    let colour = match kind {
                        Syntax::Plain => theme::CODE_TEXT,
                        Syntax::Keyword => theme::SYNTAX_KEYWORD,
                        Syntax::Type => theme::SYNTAX_TYPE,
                        Syntax::String => theme::SYNTAX_STRING,
                        Syntax::Comment => theme::TEXT_QUIET,
                    };
                    ink.text(
                        [page.left, y],
                        colour,
                        through,
                        [
                            x0.max(page.clip[0]),
                            page.clip[1],
                            x1.min(page.clip[2]),
                            page.clip[3],
                        ],
                    );
                }
            }
        }

        /// The action row's words, measured against `width`.
        fn measure_actions(&self, ui: &Ui, state: Option<&LayerState>, width: f32) -> Actions {
            let t = theme::current();
            let (state, state_colour) = match state {
                Some(state) => (action_state(state), theme::TEXT),
                None => ("not read by the worker yet".to_owned(), theme::TEXT_QUIET),
            };
            let (note, note_colour) = if let Some(why) = &self.save_error {
                (format!("not saved: {why}"), theme::ERROR_TEXT)
            } else if self.dirty_since.is_some() || self.saving {
                ("saving...".to_owned(), theme::TEXT_QUIET)
            } else {
                (
                    "saved 300 ms after your last keystroke".to_owned(),
                    theme::TEXT_QUIET,
                )
            };
            let row = t.px(ButtonSize::ACTION.height).round();
            let gap = t.px(ACTION_GAP).round();
            let state_x = action_state_x(ui, self.reference.open);
            let state_room = (width - state_x).max(1.0);
            let state_width = theme::text_width(ui, Font::Body, &state);
            let state_line = theme::line_height(ui, Font::Body);
            let note_width = theme::text_width(ui, Font::Label, &note);
            let state_wraps = (state_width > state_room).then(|| {
                theme::with_font(ui, Font::Body, || {
                    ui.calc_text_size_with_opts(&state, false, state_room)[1]
                })
            });
            let note_inline =
                state_wraps.is_none() && state_x + state_width + gap + note_width <= width;
            let first = row.max((row - state_line) * 0.5 + state_wraps.unwrap_or(state_line));
            let height = if note_inline {
                first
            } else {
                first + gap + theme::line_height(ui, Font::Label)
            };
            Actions {
                state,
                state_colour,
                note,
                note_colour,
                state_wraps,
                note_inline,
                height: height.ceil(),
            }
        }

        /// Compile now, the layer's light and state, and the save note at the right: one line
        /// when it fits, the state wrapped and the note under it when not.
        fn draw_actions(
            &mut self,
            ui: &Ui,
            dir: &Path,
            lamp: Light,
            actions: &Actions,
            width: f32,
        ) {
            let t = theme::current();
            let at = round2(ui.cursor_screen_pos());
            let row = t.px(ButtonSize::ACTION.height).round();
            if theme::button(ui, "Compile now", ButtonKind::Primary, ButtonSize::ACTION) {
                self.flush(dir);
                worker::request_rescan();
            }
            theme::register_item(ui, "compile_button");
            if ui.is_item_hovered() {
                theme::tooltip(ui, &[COMPILE_TIP]);
            }
            let compile =
                theme::button_width(ui, "Compile now", ButtonKind::Primary, ButtonSize::ACTION);
            ui.set_cursor_screen_pos([at[0] + compile + t.px(ACTION_GAP).round(), at[1]]);
            let open = self.reference.open;
            if theme::toggle_button(
                ui,
                REFERENCE_LABEL,
                open,
                ButtonKind::Primary,
                ButtonSize::ACTION,
            ) {
                if open {
                    self.reference.close();
                } else {
                    self.open_reference();
                }
            }
            if ui.is_item_hovered() {
                theme::tooltip(ui, &[reference_panel::BUTTON_TIP]);
            }
            let paint = theme::painter(ui);
            let state_x = at[0] + action_state_x(ui, open);
            let dot = t.px(theme::DOT_SIZE).round();
            let gap = t.px(STATUS_GAP).round();
            // The light is an item a widget row tall, its dot centred, so centring the row centres
            // the dot on the button.
            let light_top = at[1] + ((row - ui.frame_height()) * 0.5).round();
            ui.set_cursor_screen_pos([state_x - gap - dot, light_top]);
            theme::light_dot(ui, lamp);
            let state_line = theme::line_height(ui, Font::Body);
            let state_top = (at[1] + (row - state_line) * 0.5).round();
            match actions.state_wraps {
                None => theme::with_font(ui, Font::Body, || {
                    paint.text([state_x, state_top], actions.state_colour, &actions.state);
                }),
                Some(_) => {
                    ui.set_cursor_screen_pos([state_x, state_top]);
                    let wrap_at = at[0] + width - ui.window_pos()[0] + ui.scroll_x();
                    let _wrap = ui.push_text_wrap_pos_with_pos(wrap_at);
                    theme::text_wrapped(ui, Font::Body, actions.state_colour, &actions.state);
                }
            }
            theme::with_font(ui, Font::Label, || {
                let size = ui.calc_text_size(&actions.note);
                let y = if actions.note_inline {
                    at[1] + (row - size[1]) * 0.5
                } else {
                    at[1] + actions.height - size[1]
                };
                paint.text(
                    [at[0] + width - size[0], y.round()],
                    actions.note_colour,
                    &actions.note,
                );
            });
            ui.set_cursor_screen_pos(at);
            ui.dummy([width, actions.height.max(1.0)]);
        }

        /// The card's messages, wrapped to `width`, and how tall the card is.
        fn measure_messages(&self, ui: &Ui, file: &str, lamp: Light, width: f32) -> Messages {
            let t = theme::current();
            let style = CardStyle::MESSAGES;
            let stroke = t.stroke();
            let pad = style.pad.map(|pad| t.px(pad).round());
            let inner = (width - 2.0 * (stroke + pad[0])).max(1.0);
            let mut rows = Vec::new();
            for diagnostic in &self.diagnostics {
                let notes = diagnostic.notes.iter().map(|note| (note, true));
                for (shown, is_note) in std::iter::once((diagnostic, false)).chain(notes) {
                    rows.push(message(shown, is_note, file));
                }
            }
            for line in &self.other_lines {
                rows.push(plain_message(line.clone()));
            }
            if rows.is_empty() {
                rows.push(plain_message(
                    if lamp == Light::Live {
                        "No messages."
                    } else {
                        "No messages yet."
                    }
                    .to_owned(),
                ));
            }
            theme::with_font(ui, Font::Mono, || {
                let line = ui.text_line_height();
                let gap = t.px(MESSAGE_GAP).round();
                let most = DIAGNOSTIC_LINES * (line + gap) - gap;
                let measure = |rows: &mut Vec<Message>, room: f32| {
                    let mut content = 0.0;
                    for (index, row) in rows.iter_mut().enumerate() {
                        let room = (room - t.px(row.indent).round()).max(1.0);
                        row.lines = wrap(&row.text, room, |part| ui.calc_text_size(part)[0]);
                        if index > 0 {
                            content += gap;
                        }
                        content += row.lines.len() as f32 * line;
                    }
                    content
                };
                let mut content = measure(&mut rows, inner);
                if content > most {
                    content = measure(&mut rows, inner - t.px(theme::SCROLLBAR_SIZE).round());
                }
                let shown = content.min(most).ceil();
                Messages {
                    rows,
                    line,
                    content,
                    shown,
                    height: shown + 2.0 * (stroke + pad[1]),
                }
            })
        }

        /// The compiler messages card: `#17181D` behind a 1-pixel border, padded 8 and 10, its
        /// messages in 12-pixel mono 4 apart, scrolling past [`DIAGNOSTIC_LINES`] lines. A
        /// location in this file is a link that puts the cursor on it.
        fn draw_messages(&mut self, ui: &Ui, messages: &Messages, width: f32) {
            let t = theme::current();
            let style = CardStyle::MESSAGES;
            let stroke = t.stroke();
            let pad = style.pad.map(|pad| t.px(pad).round());
            let at = round2(ui.cursor_screen_pos());
            let max = [at[0] + width, at[1] + messages.height];
            let paint = theme::painter(ui);
            let radius = t.px(style.radius);
            paint.fill(at, max, theme::CARD_BG, radius);
            paint.outline(at, max, theme::CARD_BORDER, radius);
            ui.set_cursor_screen_pos([at[0] + stroke + pad[0], at[1] + stroke + pad[1]]);
            let inner = [(width - 2.0 * (stroke + pad[0])).max(1.0), messages.shown];
            let clicked = theme::region(ui, "##compiler-messages", [0.0, 0.0], inner, || {
                let _spacing = theme::spacing(ui, MESSAGE_GAP);
                let room = if messages.content > messages.shown {
                    inner[0] - t.px(theme::SCROLLBAR_SIZE).round()
                } else {
                    inner[0]
                };
                theme::with_font(ui, Font::Mono, || {
                    let mut clicked = None;
                    for (index, row) in messages.rows.iter().enumerate() {
                        if draw_message(ui, index, row, messages.line, room) {
                            clicked = row.jump;
                        }
                    }
                    clicked
                })
            })
            .flatten();
            if let Some((line, column)) = clicked
                && let Some(at) = jump_target(&self.text, line, column)
            {
                self.jump = Some(at);
                self.focus = true;
            }
            ui.set_cursor_screen_pos(at);
            ui.dummy([width, messages.height.max(1.0)]);
        }
    }

    /// Where the code's lines go in the view.
    struct Page {
        /// The text's left edge, scrolled; the first line's top, scrolled; what may be drawn.
        left: f32,
        top: f32,
        clip: [f32; 4],
    }

    impl Page {
        fn row_top(&self, row: usize, m: &Metrics) -> f32 {
            self.top + row as f32 * m.line
        }
    }

    /// The gutter's numbers for the lines shown, right-aligned at `right`, the first error's in
    /// red, drawn only inside `gutter` (`[x0, y0, x1, y1]`).
    fn draw_numbers(ui: &Ui, m: &Metrics, rows: &Rows, right: f32, gutter: [f32; 4]) {
        let ink = Ink::here(ui, m);
        for row in rows.shown.clone() {
            let number = (row + 1).to_string();
            let colour = if rows.error == Some(row) {
                theme::ERROR_TEXT
            } else {
                theme::TEXT_GUTTER
            };
            let y = rows.top + row as f32 * m.line + m.lead;
            ink.text([right - m.width(&number), y], colour, &number, gutter);
        }
    }

    /// The first line of an item's detail: a function's first signature.
    fn first_line(detail: &str) -> &str {
        detail.lines().next().unwrap_or_default()
    }

    /// The completion popup for `session` on the foreground draw list, so the field keeps the
    /// keyboard: under the word it completes, or over its line near the code area's bottom
    /// (`area`, `[x0, y0, x1, y1]`), and nothing while that line is out of view. A card of rows,
    /// each a quiet tag, the name and the first line of its detail at the right, the selected row
    /// gold; `+N more` under them when more matched; and under a rule the selected item's other
    /// signatures and its hint, wrapped. JetBrains Mono 12 throughout, the names over the word.
    fn draw_completion(
        ui: &Ui,
        text: &str,
        session: &Session,
        layout: &Layout,
        page: &Page,
        area: [f32; 4],
        m: &Metrics,
    ) {
        let completion = session.completion();
        let start = completion.range.start;
        let row = layout.row_of(start);
        let (Some(line), Some(selected)) = (layout.lines.get(row), session.selected_item()) else {
            return;
        };
        let Some(before) = text.get(line.start..start) else {
            return;
        };
        let word = [page.left + m.width(before), page.row_top(row, m)];
        if !popup_shows(word, m.line, area) {
            return;
        }
        let t = theme::current();
        let _font = theme::font(ui, Font::Mono);
        let px = |value: f32| t.px(value).round();
        let pad = theme::COMPLETION_PAD.map(px);
        let row_pad = theme::COMPLETION_ROW_PAD.map(px);
        let gap = px(theme::COMPLETION_GAP);
        let line_height = ui.text_line_height();
        let row_height = line_height + 2.0 * row_pad[1];
        let measure = |words: &str| ui.calc_text_size(words)[0];
        let items = &completion.items;
        let widest = |sizes: &mut dyn Iterator<Item = f32>| sizes.fold(0.0, f32::max);
        let tag = widest(&mut items.iter().map(|item| measure(item.kind.tag())));
        let name = widest(&mut items.iter().map(|item| measure(&item.name)));
        let detail = widest(&mut items.iter().map(|item| measure(first_line(&item.detail))));
        let columns = tag + gap + name + if detail > 0.0 { gap + detail } else { 0.0 };
        let display = ui.io().display_size;
        let width = (columns + 2.0 * (pad[0] + row_pad[0]))
            .clamp(
                px(theme::COMPLETION_MIN_WIDTH),
                px(theme::COMPLETION_MAX_WIDTH),
            )
            .min(display[0])
            .max(1.0);
        let inner = (width - 2.0 * (pad[0] + row_pad[0])).max(1.0);

        // Under the rows: the selected item's other signatures, and its hint wrapped.
        let mut notes: Vec<String> = selected
            .detail
            .lines()
            .skip(1)
            .map(|signature| theme::fit_text(ui, Font::Mono, signature, inner))
            .collect();
        if !selected.hint.is_empty() {
            notes.extend(
                wrap(&selected.hint, inner, measure)
                    .into_iter()
                    .filter_map(|range| selected.hint.get(range).map(str::to_owned))
                    .take(theme::COMPLETION_HINT_LINES),
            );
        }
        let more = (completion.more > 0).then(|| format!("+{} more", completion.more));
        let rows = items.len() + usize::from(more.is_some());
        let notes_height = if notes.is_empty() {
            0.0
        } else {
            2.0 * pad[1] + t.stroke() + notes.len() as f32 * line_height
        };
        let height = 2.0 * pad[1] + rows as f32 * row_height + notes_height;
        let left = word[0] - pad[0] - row_pad[0] - tag - gap;
        let origin = popup_origin([left, word[1]], m.line, [width, height], area, display);
        let end = [origin[0] + width, origin[1] + height];

        let paint = theme::foreground_painter(ui);
        let radius = t.px(theme::COMPLETION_RADIUS);
        paint.fill(origin, end, theme::COMPLETION_BG, radius);
        paint.outline(origin, end, theme::COMPLETION_BORDER, radius);
        let x = origin[0] + pad[0] + row_pad[0];
        let right = end[0] - pad[0] - row_pad[0];
        let name_x = x + tag + gap;
        let mut y = origin[1] + pad[1];
        for (index, item) in items.iter().enumerate() {
            let chosen = index == session.selected();
            if chosen {
                paint.fill(
                    [origin[0] + pad[0], y],
                    [end[0] - pad[0], y + row_height],
                    theme::COMPLETION_SELECTED_BG,
                    (radius - t.stroke()).max(0.0),
                );
            }
            let top = y + row_pad[1];
            paint.text([x, top], theme::COMPLETION_QUIET, item.kind.tag());
            let shown = theme::fit_text(ui, Font::Mono, &item.name, (right - name_x).max(1.0));
            let colour = if chosen {
                theme::COMPLETION_SELECTED_TEXT
            } else {
                theme::COMPLETION_TEXT
            };
            paint.text([name_x, top], colour, &shown);
            let room = right - (name_x + measure(&shown) + gap);
            let signature = first_line(&item.detail);
            if room > 0.0 && !signature.is_empty() {
                let fitted = theme::fit_text(ui, Font::Mono, signature, room);
                paint.text(
                    [right - measure(&fitted), top],
                    theme::COMPLETION_QUIET,
                    &fitted,
                );
            }
            y += row_height;
        }
        if let Some(more) = &more {
            paint.text([x, y + row_pad[1]], theme::COMPLETION_QUIET, more);
            y += row_height;
        }
        if !notes.is_empty() {
            y += pad[1];
            paint.hline(
                origin[0] + pad[0],
                end[0] - pad[0],
                y,
                theme::COMPLETION_BORDER,
            );
            y += t.stroke() + pad[1];
            for note in &notes {
                paint.text([x, y], theme::COMPLETION_QUIET, note);
                y += line_height;
            }
        }
        theme::register_rect("completion_popup", [origin[0], origin[1], width, height]);
    }

    /// `words` in the code area's text position, wrapped at its padding: while the file is read,
    /// or why it cannot be edited.
    fn say_in_area(ui: &Ui, view: [[f32; 2]; 2], m: &Metrics, colour: [f32; 4], words: &str) {
        let [min, max] = view;
        ui.set_cursor_screen_pos([min[0] + m.pad[0], min[1] + m.pad[1]]);
        let wrap_at = max[0] - m.pad[0] - ui.window_pos()[0] + ui.scroll_x();
        let _wrap = ui.push_text_wrap_pos_with_pos(wrap_at);
        theme::text_wrapped(ui, Font::Mono, colour, words);
    }

    /// Where the action row's state starts, from its left: after Compile now, the row's gap, the
    /// Reference button as it is drawn while the reference is `open` or not, the row's gap, the
    /// light and the gap beside it.
    fn action_state_x(ui: &Ui, open: bool) -> f32 {
        let t = theme::current();
        let button =
            theme::button_width(ui, "Compile now", ButtonKind::Primary, ButtonSize::ACTION);
        let kind = if open {
            ButtonKind::Primary
        } else {
            ButtonKind::Secondary
        };
        let reference = theme::button_width(ui, REFERENCE_LABEL, kind, ButtonSize::ACTION);
        let gap = t.px(ACTION_GAP).round();
        button + gap + reference + gap + t.px(theme::DOT_SIZE).round() + t.px(STATUS_GAP).round()
    }

    /// One diagnostic as a card message: its location in red for an error, amber for a warning,
    /// the rest in the body colour; a note set in and quiet throughout.
    fn message(shown: &Diagnostic, is_note: bool, file: &str) -> Message {
        let location = location(shown).unwrap_or_default();
        let rest = after_location(shown);
        let (location_colour, rest_colour) = match shown.severity {
            _ if is_note => (theme::TEXT_QUIET, theme::TEXT_QUIET),
            Severity::Error => (theme::ERROR_TEXT, theme::TEXT),
            Severity::Warning => (theme::WARNING_TEXT, theme::TEXT),
            Severity::Note => (theme::TEXT_QUIET, theme::TEXT_QUIET),
        };
        let jump = (shown.layer_file() == Some(file) && shown.line > 0)
            .then_some((shown.line, shown.column));
        Message {
            split: location.len(),
            text: location + &rest,
            location_colour,
            rest_colour,
            indent: if is_note { NOTE_INDENT } else { 0.0 },
            lines: Vec::new(),
            jump,
        }
    }

    /// A line of the messages file that is not a diagnostic, or the card's empty note: quiet.
    fn plain_message(text: String) -> Message {
        Message {
            text,
            split: 0,
            location_colour: theme::TEXT_QUIET,
            rest_colour: theme::TEXT_QUIET,
            indent: 0.0,
            lines: Vec::new(),
            jump: None,
        }
    }

    /// One message at the cursor, `width` wide, its lines `line` apart. A message that names a
    /// line of this file is a button whose location is a link, underlined in its colour as the
    /// mockup's links are; under the mouse the pointer is a hand. Returns whether it was clicked.
    fn draw_message(ui: &Ui, index: usize, row: &Message, line: f32, width: f32) -> bool {
        let t = theme::current();
        let at = round2(ui.cursor_screen_pos());
        let size = [width.max(1.0), (row.lines.len() as f32 * line).max(1.0)];
        let clicked = if row.jump.is_some() {
            ui.invisible_button(format!("##message{index}"), size)
        } else {
            ui.dummy(size);
            false
        };
        let hovered = row.jump.is_some() && ui.is_item_hovered();
        if hovered {
            ui.set_mouse_cursor(Some(MouseCursor::Hand));
        }
        let paint = theme::painter(ui);
        let left = at[0] + t.px(row.indent).round();
        for (index, part) in row.lines.iter().enumerate() {
            let y = at[1] + index as f32 * line;
            let split = row.split.clamp(part.start, part.end);
            let (Some(linked), Some(rest)) = (
                row.text.get(part.start..split),
                row.text.get(split..part.end),
            ) else {
                continue;
            };
            let linked_width = ui.calc_text_size(linked)[0];
            if !linked.is_empty() {
                paint.text([left, y], row.location_colour, linked);
                if row.jump.is_some() {
                    let under = (y + line - t.px(LINK_UNDERLINE)).round();
                    paint.hline(left, left + linked_width, under, row.location_colour);
                }
            }
            if !rest.is_empty() {
                paint.text([left + linked_width, y], row.rest_colour, rest);
            }
        }
        clicked
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

    /// `text`'s runs as `(word, kind)` pairs, the plain ones left out.
    fn coloured(text: &str) -> Vec<(&str, Syntax)> {
        highlight(text)
            .into_iter()
            .flatten()
            .filter(|(_, kind)| *kind != Syntax::Plain)
            .map(|(range, kind)| (&text[range], kind))
            .collect()
    }

    #[test]
    fn the_canvas_example_is_coloured_as_the_canvas_colours_it() {
        // Code.dc.html's ten lines, and every run it colours, in order: a type where one is
        // declared, never in a constructor call (`= float3(0.0, ...)`), and the editor's
        // `TonemapCtx` plain.
        let text = "uniform float  Strength < ui_type = \"slider\";\n\
                    \x20                ui_min = 0.0; ui_max = 1.0; > = 0.6;\n\
                    uniform float3 Shadows  < ui_type = \"color\";\n\
                    \x20                ui_category = \"Lift\"; > = float3(0.0, 0.02, 0.05);\n\
                    uniform int    Mode     < ui_type = \"combo\";\n\
                    \x20                ui_items = \"Soft\\0Hard\\0\"; > = 0;\n\
                    \n\
                    float3 lift(float3 c)\n\
                    { return c + Shadow * (1.0 - c); }\n\
                    float3 apply(float3 c, TonemapCtx ctx) { return lerp(c, lift(c), Strength); }";
        assert_eq!(
            coloured(text),
            vec![
                ("uniform", Syntax::Keyword),
                ("float", Syntax::Type),
                ("\"slider\"", Syntax::String),
                ("uniform", Syntax::Keyword),
                ("float3", Syntax::Type),
                ("\"color\"", Syntax::String),
                ("\"Lift\"", Syntax::String),
                ("uniform", Syntax::Keyword),
                ("int", Syntax::Type),
                ("\"combo\"", Syntax::String),
                ("\"Soft\\0Hard\\0\"", Syntax::String),
                ("float3", Syntax::Type),
                ("float3", Syntax::Type),
                ("return", Syntax::Keyword),
                ("float3", Syntax::Type),
                ("float3", Syntax::Type),
                ("return", Syntax::Keyword),
            ]
        );
    }

    #[test]
    fn the_action_row_says_what_the_state_means_for_the_frame() {
        let error = |running| LayerState::Error {
            summary: "layers/3-warm-grade.hlsl:9:16: error: use of undeclared identifier"
                .to_owned(),
            running,
        };
        assert_eq!(
            action_state(&error(true)),
            "Error: the last good version is still drawing"
        );
        // The compiler's words are the card's, not the row's.
        assert!(!action_state(&error(false)).contains("undeclared"));
        let live = LayerState::Live {
            compile_ms: 24.0,
            verified: 7,
        };
        assert_eq!(action_state(&live), live.label());
        assert_eq!(action_state(&LayerState::Pending), "Compiling");
        assert!(
            action_state(&LayerState::NoCompiler {
                why: "x".to_owned()
            })
            .ends_with('x')
        );
    }

    #[test]
    fn names_are_coloured_by_what_they_are() {
        for word in [
            "float",
            "float3",
            "float4x4",
            "int2",
            "uint",
            "half3",
            "bool",
            "matrix",
            "void",
            "min16float2",
        ] {
            assert_eq!(classify(word), Syntax::Type, "{word}");
        }
        for word in [
            "in", "out", "inout", "static", "const", "struct", "while", "break",
        ] {
            assert_eq!(classify(word), Syntax::Keyword, "{word}");
        }
        for word in [
            "float5",
            "float4x5",
            "floats",
            "interp",
            "input",
            "lerp",
            "Strength",
            "int0",
            "",
            "TonemapCtx",
            "LightingCtx",
        ] {
            assert_eq!(classify(word), Syntax::Plain, "{word}");
        }
        // A type called as a constructor is plain; a cast and a declaration are not.
        assert_eq!(
            coloured("x = float3(0.0); y = (float)x; half2 z;"),
            vec![("float", Syntax::Type), ("half2", Syntax::Type)]
        );
    }

    #[test]
    fn comments_and_strings_keep_their_own_colour() {
        assert_eq!(
            coloured("x = 1; // return float"),
            vec![("// return float", Syntax::Comment)]
        );
        assert_eq!(
            coloured("s = \"in \\\"out\\\" float\"; uint"),
            vec![
                ("\"in \\\"out\\\" float\"", Syntax::String),
                ("uint", Syntax::Type)
            ]
        );
        // A block comment runs across lines, and the code after it is coloured again.
        let text = "a /* if\nfloat */ return";
        assert_eq!(
            coloured(text),
            vec![
                ("/* if", Syntax::Comment),
                ("float */", Syntax::Comment),
                ("return", Syntax::Keyword),
            ]
        );
        // A string left open ends with its line.
        assert_eq!(
            coloured("\"open\nfloat"),
            vec![("\"open", Syntax::String), ("float", Syntax::Type)]
        );
    }

    #[test]
    fn runs_cover_every_line_of_every_template_once() {
        let mut texts: Vec<&str> = crate::editor::templates::TEMPLATES
            .iter()
            .map(|template| template.text)
            .collect();
        texts.push("\u{e9}t\u{e9} float \"\u{e9}\\\u{e9}\" /* \u{2014} */ x");
        texts.push("");
        for text in texts {
            let lines = line_ranges(text);
            let runs = highlight(text);
            assert_eq!(lines.len(), runs.len());
            for (line, runs) in lines.iter().zip(&runs) {
                let mut at = line.start;
                for (run, _) in runs {
                    assert_eq!(run.start, at, "a gap in {:?}", &text[line.clone()]);
                    assert!(!run.is_empty());
                    assert!(
                        text.get(run.clone()).is_some(),
                        "{run:?} splits a character"
                    );
                    at = run.end;
                }
                assert_eq!(at, line.end);
                // Neighbours differ: a run of one colour is one run.
                assert!(runs.windows(2).all(|pair| pair[0].1 != pair[1].1));
            }
        }
    }

    #[test]
    fn the_wave_goes_under_the_name_the_error_names() {
        let text = "float3 Shadows;\n{ return c + Shadow * (1.0 - c); }\n";
        let span = error_span(text, 2, Some(14));
        assert_eq!(span.map(|span| &text[span]), Some("Shadow"));
        // Punctuation: the one character.
        assert_eq!(error_span(text, 2, Some(1)).map(|s| &text[s]), Some("{"));
        // No column, the end of the line, a line that is not there.
        assert_eq!(error_span(text, 2, None), None);
        assert_eq!(error_span(text, 1, Some(16)), None);
        assert_eq!(error_span(text, 9, Some(1)), None);
        // A two-byte character is underlined whole.
        assert_eq!(error_span("\u{e9}x", 1, Some(1)), Some(0..2));
    }

    #[test]
    fn the_first_error_in_this_file_is_the_one_marked() {
        let found = |line, column, severity, file: &str| {
            Diagnostic::in_layer(1, file, line, column, severity, "m")
        };
        let text = "a\nb c\n";
        let diagnostics = [
            found(1, 1, Severity::Warning, "x.hlsl"),
            found(2, 3, Severity::Error, "other.hlsl"),
            found(2, 3, Severity::Error, "x.hlsl"),
            found(1, 1, Severity::Error, "x.hlsl"),
        ];
        assert_eq!(
            first_error(&diagnostics, "x.hlsl", text),
            Some((1, Some(4..5)))
        );
        assert_eq!(first_error(&diagnostics[..2], "x.hlsl", text), None);
        // A line past the text, as diagnostics of an older version can name.
        let stale = [found(7, 1, Severity::Error, "x.hlsl")];
        assert_eq!(first_error(&stale, "x.hlsl", text), None);
    }

    #[test]
    fn a_message_splits_at_its_location() {
        let mut found = diagnostics_of(
            "layers/3-warm-grade.hlsl:9:16: error: use of undeclared identifier 'Shadow'",
        );
        assert_eq!(
            location(&found).as_deref(),
            Some("layers/3-warm-grade.hlsl:9:16")
        );
        assert_eq!(
            after_location(&found),
            ": error: use of undeclared identifier 'Shadow'"
        );
        found.column = None;
        assert_eq!(
            location(&found).as_deref(),
            Some("layers/3-warm-grade.hlsl:9")
        );
        let bare = diagnostics_of("error: validation errors");
        assert_eq!(location(&bare), None);
        assert_eq!(after_location(&bare), "error: validation errors");
    }

    fn diagnostics_of(line: &str) -> Diagnostic {
        match crate::editor::diagnostics::parse_line(line) {
            Some(found) => found,
            None => panic!("{line:?} is a diagnostic"),
        }
    }

    #[test]
    fn the_windows_path_names_the_game_folder() {
        let game = r"Z:\games\ELDEN RING\Game";
        let path = r"Z:\games\ELDEN RING\Game\er-shader-editor\layers\3-warm-grade.hlsl";
        assert_eq!(
            game_relative(path, game),
            r"<Game>\er-shader-editor\layers\3-warm-grade.hlsl"
        );
        assert_eq!(
            game_relative(path, &format!("{game}\\")),
            game_relative(path, game)
        );
        // A folder whose name only starts with the game's, and a path elsewhere, stay whole.
        assert_eq!(
            game_relative(r"Z:\games\ELDEN RING\Gamey\x", game),
            r"Z:\games\ELDEN RING\Gamey\x"
        );
        assert_eq!(game_relative(r"C:\x", game), r"C:\x");
        assert_eq!(game_relative("/x", "/"), "/x");
    }

    #[test]
    fn a_long_path_loses_its_front_first() {
        let path = "/home/user/.local/share/Steam/steamapps/common/ELDEN RING/Game/a.hlsl";
        let within = |chars: usize| move |text: &str| text.chars().count() <= chars;
        assert_eq!(elide_front(path, "...", within(200)), path);
        assert_eq!(
            elide_front(path, "...", within(27)),
            "/.../ELDEN RING/Game/a.hlsl"
        );
        // Nothing fits: the last component after the ellipsis.
        assert_eq!(elide_front(path, "...", within(3)), "/.../a.hlsl");
        assert_eq!(
            elide_front(r"<Game>\er-shader-editor\layers\x.hlsl", "...", within(20)),
            r"<Game>\...\x.hlsl"
        );
        // A path of one component has nothing to drop.
        assert_eq!(elide_front("x.hlsl", "...", within(2)), "x.hlsl");
    }

    #[test]
    fn a_message_wraps_at_spaces_like_a_paragraph() {
        let width = |text: &str| text.chars().count() as f32;
        let text = "error: use of undeclared identifier";
        let lines: Vec<&str> = wrap(text, 12.0, width)
            .into_iter()
            .map(|line| &text[line])
            .collect();
        assert_eq!(lines, vec!["error: use", "of", "undeclared", "identifier"]);
        // A word longer than the line breaks where it overflows.
        let lines: Vec<&str> = wrap("abcdefgh", 3.0, width)
            .into_iter()
            .map(|line| &"abcdefgh"[line])
            .collect();
        assert_eq!(lines, vec!["abc", "def", "gh"]);
        assert_eq!(wrap("", 3.0, width), vec![0..0]);
        assert_eq!(wrap("ab", 0.0, width), vec![0..1, 1..2]);
        assert_eq!(wrap("\u{e9}\u{e9}", 1.0, width), vec![0..2, 2..4]);
    }

    #[test]
    fn only_the_rows_in_view_are_drawn() {
        // Rows 22 tall from y = 100, a view from 150 to 240.
        assert_eq!(visible_rows(100.0, 22.0, 50, 150.0, 240.0), 2..7);
        // Scrolled past the top: the first row is above the view.
        assert_eq!(visible_rows(-30.0, 22.0, 50, 0.0, 44.0), 1..4);
        assert_eq!(visible_rows(100.0, 22.0, 3, 0.0, 1000.0), 0..3);
        assert_eq!(visible_rows(100.0, 22.0, 0, 0.0, 1000.0), 0..0);
        assert_eq!(visible_rows(100.0, 0.0, 5, 0.0, 1000.0), 0..0);
        assert_eq!(visible_rows(100.0, f32::NAN, 5, 0.0, 1000.0), 0..0);
    }

    #[test]
    fn the_view_follows_the_cursor_a_quarter_at_a_time() {
        // In view: unchanged.
        assert_eq!(follow(0.0, 300.0, 600.0, 150.0), 0.0);
        // Past the right edge: a quarter past it.
        assert_eq!(follow(0.0, 650.0, 600.0, 150.0), 200.0);
        // Left of the scroll: a quarter before it, never below 0.
        assert_eq!(follow(400.0, 300.0, 600.0, 150.0), 150.0);
        assert_eq!(follow(100.0, 20.0, 600.0, 150.0), 0.0);
    }

    #[test]
    fn the_cursor_blinks_as_dear_imgui_blinks_it() {
        assert!(blink_on(0.0, true));
        assert!(blink_on(0.5, true));
        assert!(!blink_on(1.0, true));
        assert!(blink_on(1.3, true));
        assert!(blink_on(1.0, false));
    }

    #[test]
    fn the_popup_shows_only_while_its_line_is_in_view() {
        let area = [100.0, 50.0, 700.0, 450.0];
        assert!(popup_shows([200.0, 50.0], 22.0, area));
        assert!(popup_shows([200.0, 428.0], 22.0, area));
        // Scrolled partly off the top or the bottom, or sideways out of the view.
        assert!(!popup_shows([200.0, 40.0], 22.0, area));
        assert!(!popup_shows([200.0, 440.0], 22.0, area));
        assert!(!popup_shows([90.0, 200.0], 22.0, area));
        assert!(!popup_shows([710.0, 200.0], 22.0, area));
    }

    #[test]
    fn the_popup_goes_under_its_line_and_over_it_near_the_bottom() {
        let area = [100.0, 50.0, 700.0, 450.0];
        let display = [1920.0, 1080.0];
        // Room below: under the line.
        assert_eq!(
            popup_origin([150.0, 100.0], 22.0, [300.0, 150.0], area, display),
            [150.0, 122.0]
        );
        // Near the bottom: over the line.
        assert_eq!(
            popup_origin([150.0, 400.0], 22.0, [300.0, 150.0], area, display),
            [150.0, 250.0]
        );
        // Fits neither way: the side with more room, under here.
        assert_eq!(
            popup_origin([150.0, 150.0], 22.0, [300.0, 500.0], area, display),
            [150.0, 172.0]
        );
        // And over it when there is more room above.
        assert_eq!(
            popup_origin([150.0, 380.0], 22.0, [300.0, 500.0], area, display),
            [150.0, 0.0]
        );
        // Kept on the display: pulled left from the right edge, never off the left.
        assert_eq!(
            popup_origin([1800.0, 100.0], 22.0, [300.0, 150.0], area, display),
            [1620.0, 122.0]
        );
        assert_eq!(
            popup_origin([-40.0, 100.0], 22.0, [300.0, 150.0], area, display),
            [0.0, 122.0]
        );
    }
}
