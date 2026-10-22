//! The Code sub-tab's reference: a column beside the code area listing everything a layer's code
//! can use (`crate::editor::reference`), searchable, so a name is looked up rather than guessed.
//!
//! # Layout
//!
//! A card in the completion popup's colors, [`REFERENCE_SHARE`] of the sub-tab's width between
//! [`REFERENCE_MIN_WIDTH`] and [`REFERENCE_WIDTH`] at 1080p, as tall as the code area
//! ([`column_width`]). At its top the search field, a frame high in JetBrains Mono; under it a
//! line saying which stage the list is kept to and how many entries it holds, with All stages at
//! its right; then the list, which scrolls: a heading per group, folded with a click,
//! and under an open one its entries, each a quiet tag for its kind, its name, and its signature
//! or value at the right. A search opens every group. An entry not at the layer's stage, shown
//! with All stages, is dimmed and has the stages it belongs to at the right instead
//! ([`stage_note`]), and a name pasted from elsewhere that a layer cannot use is in the error
//! color. Only the rows in view are drawn ([`rows_in_view`]).
//!
//! # Use
//!
//! Hovering an entry shows its signatures, what it is, the stages it can be written at and what a
//! click does. A click writes it at the editor's cursor, through the edit path completion uses
//! (`code.rs` applies `reference::insertion` in the text field's callback, as it applies an
//! accepted completion): the field takes the keyboard back on the next frame with its cursor where
//! it was, and the entry goes in there. A mask term is copied instead, for the Selector tab.
//!
//! The keyboard reaches all of it. [`KEY`] opens the column with its search field taking the
//! keyboard, and from the search field gives the keyboard back to the code. In the search field
//! Up and Down move a highlight through the entries, Enter writes the highlighted one (the first
//! when none is), and Escape closes the column; Escape while neither the code nor the search has
//! the keyboard closes it too.
//!
//! # Telemetry
//!
//! Each frame the column is drawn records it in the panel's frame record
//! ([`super::panel_record::ReferenceSnapshot`]:
//! open, its search field focused, its stage, its query's length, the entries it kept and all it
//! has) and its rectangle as `reference_panel`, its search field as `reference_search`; the
//! counters `editor_reference_opens` and `editor_reference_inserts` count openings and entries
//! written.

use std::ops::Range;

use crate::editor::LayerStage;
use crate::editor::complete::{Item, ItemKind};
use crate::editor::reference::Row;

/// The key that opens the column and puts the keyboard in its search field.
pub const KEY: &str = "F1";

// The column's sizes at 1080p, beside the code area: a card the completion popup's colors, its
// search field a frame high, its groups' headings and entries in rows.

/// The column's widest and narrowest, and the share of the sub-tab's width it takes between.
pub const REFERENCE_WIDTH: f32 = 300.0;
pub const REFERENCE_MIN_WIDTH: f32 = 220.0;
pub const REFERENCE_SHARE: f32 = 0.42;
/// Inside the card, and between its search field, its summary line and its list.
pub const REFERENCE_PAD: [f32; 2] = [8.0, 8.0];
pub const REFERENCE_GAP: f32 = 6.0;
/// A group heading's row and an entry's row.
pub const REFERENCE_HEADING_HEIGHT: f32 = 24.0;
pub const REFERENCE_ROW_HEIGHT: f32 = 20.0;
/// How far an entry is set in under its heading, and its kind's tag column from its name.
pub const REFERENCE_INDENT: f32 = 14.0;
pub const REFERENCE_TAG_GAP: f32 = 8.0;
/// A heading's chevron: its half size.
pub const REFERENCE_CHEVRON: f32 = 3.0;

/// What the Reference button says under the mouse.
pub const BUTTON_TIP: &str = "Everything a layer's code can use, searchable. F1 opens it with its \
                              search field ready; Escape in the field closes it.";

/// The column's width, in screen pixels, beside a sub-tab `width` wide at the panel's `scale`:
/// its share of the width, kept between its narrowest and its widest, and never more than half.
#[must_use]
pub fn column_width(width: f32, scale: f32) -> f32 {
    let narrowest = REFERENCE_MIN_WIDTH * scale;
    let widest = REFERENCE_WIDTH * scale;
    (width * REFERENCE_SHARE)
        .max(narrowest)
        .min(widest)
        .min(width * 0.5)
        .floor()
        .max(1.0)
}

/// The height of row `row` of the list: a heading's or an entry's.
#[must_use]
pub fn row_height(row: &Row, heading: f32, entry: f32) -> f32 {
    match row {
        Row::Heading { .. } => heading,
        Row::Entry(_) => entry,
    }
}

/// The top of each row from the list's top, and the list's whole height.
#[must_use]
pub fn row_tops(rows: &[Row], heading: f32, entry: f32) -> (Vec<f32>, f32) {
    let mut tops = Vec::with_capacity(rows.len());
    let mut y = 0.0;
    for row in rows {
        tops.push(y);
        y += row_height(row, heading, entry);
    }
    (tops, y)
}

/// The rows any part of which lies between `from` and `to`, from the list's top: the ones a
/// frame draws.
#[must_use]
pub fn rows_in_view(tops: &[f32], total: f32, from: f32, to: f32) -> Range<usize> {
    let bottom = |at: usize| tops.get(at + 1).copied().unwrap_or(total);
    let first = (0..tops.len())
        .find(|&at| bottom(at) > from)
        .unwrap_or(tops.len());
    let end = (first..tops.len())
        .find(|&at| tops[at] >= to)
        .unwrap_or(tops.len());
    first..end
}

/// The entry row after `current` among `entries` (row indices), or before it, wrapping round;
/// the first or the last when none is current. `None` when there are no entries.
#[must_use]
pub fn step(entries: &[usize], current: Option<usize>, forward: bool) -> Option<usize> {
    let count = entries.len();
    if count == 0 {
        return None;
    }
    let at = current.and_then(|row| entries.iter().position(|&entry| entry == row));
    let next = match (at, forward) {
        (None, true) => 0,
        (None, false) => count - 1,
        (Some(at), true) => (at + 1) % count,
        (Some(at), false) => (at + count - 1) % count,
    };
    entries.get(next).copied()
}

/// The scroll that brings the span from `top` to `bottom` into a view `height` tall now at
/// `scroll`, moving as little as it can.
#[must_use]
pub fn scroll_to(scroll: f32, top: f32, bottom: f32, height: f32) -> f32 {
    if top < scroll {
        top
    } else if bottom > scroll + height {
        (bottom - height).max(0.0)
    } else {
        scroll
    }
}

/// What an entry's row shows at its right in place of its signature: the stages it belongs to,
/// when All stages lists it for a layer at `stage` it cannot be written at -- `Vertex` beside
/// `er_wrap_time` in a pixel layer, `every pixel stage` beside `ddx` in a vertex one. `None` for
/// an entry at `stage`, and for a pasted name, which no stage takes.
#[must_use]
pub fn stage_note(item: &Item, stage: LayerStage) -> Option<String> {
    (item.kind != ItemKind::Unavailable && !item.stages.contains(stage))
        .then(|| item.stages.label())
}

/// The line under the search field: the stage the list is kept to, or every stage, and how many
/// entries it holds, or how many of them match the search.
#[must_use]
pub fn summary(stage: Option<&str>, shown: usize, total: usize, query: &str) -> String {
    let at = stage.map_or_else(|| "Every stage".to_owned(), str::to_owned);
    let query = query.trim();
    if query.is_empty() {
        format!("{at}: {shown} entries")
    } else {
        format!("{at}: {shown} of {total} match \"{query}\"")
    }
}

#[cfg(windows)]
pub(super) use draw::{Action, ReferenceState};

/// The column's state and drawing.
#[cfg(windows)]
mod draw {
    use std::collections::BTreeSet;

    use hudhook::imgui::{Key, Ui};

    use super::super::panel_record::ReferenceSnapshot;
    use super::super::theme::{self, Font};
    use super::{
        REFERENCE_CHEVRON, REFERENCE_GAP, REFERENCE_HEADING_HEIGHT, REFERENCE_INDENT,
        REFERENCE_PAD, REFERENCE_ROW_HEIGHT, REFERENCE_TAG_GAP, row_tops, rows_in_view, scroll_to,
        stage_note, step, summary,
    };
    use crate::editor::LayerStage;
    use crate::editor::complete::{Category, Item, ItemKind};
    use crate::editor::reference::{self, Row};

    /// What the column asks of the Code sub-tab this frame.
    #[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
    pub(in crate::menu) enum Action {
        #[default]
        None,
        /// Write the entry at the editor's cursor.
        Write(&'static Item),
        /// Copy the entry: a mask term, which the Selector tab takes.
        Copy(&'static Item),
        /// Close the column and give the keyboard back to the code.
        Close,
    }

    /// The column, open or not, and what it was left showing.
    pub(in crate::menu) struct ReferenceState {
        pub open: bool,
        query: String,
        /// The list shows every stage's entries, not only the layer's stage's.
        all_stages: bool,
        folded: BTreeSet<Category>,
        /// The search field takes the keyboard on the next frame.
        focus_search: bool,
        /// The search field had the keyboard on the last frame.
        searching: bool,
        /// The highlighted row, which Enter writes.
        highlight: Option<usize>,
        /// Scroll the highlighted row into view on the next frame.
        follow: bool,
    }

    /// The tag column's widest tag.
    const WIDEST_TAG: &str = "method";

    impl ReferenceState {
        pub(in crate::menu) fn new() -> Self {
            Self {
                open: false,
                query: String::new(),
                all_stages: false,
                // The groups a layer seldom wants start folded; a search opens them.
                folded: [
                    Category::Swizzle,
                    Category::Type,
                    Category::Keyword,
                    Category::Widget,
                    Category::Scale,
                    Category::Preprocessor,
                ]
                .into_iter()
                .collect(),
                focus_search: false,
                searching: false,
                highlight: None,
                follow: false,
            }
        }

        /// Open the column with its search field taking the keyboard.
        pub(in crate::menu) fn open_search(&mut self) {
            self.open = true;
            self.focus_search = true;
        }

        pub(in crate::menu) fn close(&mut self) {
            self.open = false;
            self.searching = false;
            self.focus_search = false;
        }

        /// Did the search field have the keyboard on the last frame?
        pub(in crate::menu) fn searching(&self) -> bool {
            self.searching
        }

        /// The column at the cursor, `size` in screen pixels, for a layer at `stage`.
        pub(in crate::menu) fn draw(
            &mut self,
            ui: &Ui,
            size: [f32; 2],
            stage: LayerStage,
        ) -> Action {
            let t = theme::current();
            let stroke = t.stroke();
            let at = ui.cursor_screen_pos().map(f32::round);
            let max = [at[0] + size[0], at[1] + size[1]];
            theme::register_rect("reference_panel", [at[0], at[1], size[0], size[1]]);
            let paint = theme::painter(ui);
            let radius = t.px(theme::FRAME_RADIUS);
            paint.fill(at, max, theme::COMPLETION_BG, radius);
            paint.outline(at, max, theme::COMPLETION_BORDER, radius);

            let pad = REFERENCE_PAD.map(|pad| t.px(pad).round());
            let gap = t.px(REFERENCE_GAP).round();
            let x = at[0] + stroke + pad[0];
            let inner = (size[0] - 2.0 * (stroke + pad[0])).max(1.0);

            let kept = (!self.all_stages).then_some(stage);
            let groups = reference::groups(&self.query, kept);
            let searched = !self.query.trim().is_empty();
            let rows = reference::rows(&groups, |c| self.folded.contains(&c), searched);
            let entries = reference::entry_rows(&rows);
            if self.highlight.is_some_and(|row| !entries.contains(&row)) {
                self.highlight = None;
            }

            // The keys the search field leaves alone, read before it is drawn.
            let mut action = Action::None;
            if self.searching {
                if ui.is_key_pressed(Key::DownArrow) {
                    self.highlight = step(&entries, self.highlight, true);
                    self.follow = true;
                } else if ui.is_key_pressed(Key::UpArrow) {
                    self.highlight = step(&entries, self.highlight, false);
                    self.follow = true;
                } else if ui.is_key_pressed(Key::Enter) || ui.is_key_pressed(Key::KeypadEnter) {
                    let row = self.highlight.or_else(|| entries.first().copied());
                    if let Some(Row::Entry(item)) = row.and_then(|row| rows.get(row)) {
                        action = activate(item);
                    }
                } else if ui.is_key_pressed(Key::Escape) {
                    action = Action::Close;
                }
            }

            ui.set_cursor_screen_pos([x, at[1] + stroke + pad[1]]);
            let before = self.query.clone();
            {
                let _mono = theme::font(ui, Font::Mono);
                let _frame = theme::frame(ui, theme::FRAME_HEIGHT, theme::MONO_FRAME_PAD_X);
                ui.set_next_item_width(inner);
                if std::mem::take(&mut self.focus_search) {
                    ui.set_keyboard_focus_here();
                }
                ui.input_text("##reference-search", &mut self.query)
                    .hint("search names and what they do")
                    .build();
            }
            theme::register_item(ui, "reference_search");
            self.searching = ui.is_item_active();
            if self.query != before {
                self.highlight = None;
            }
            let field = theme::item_rect(ui);

            // The summary, and All stages at its right, both in the label font on one line as
            // tall as the checkbox's box or the text, whichever is taller.
            let line = theme::line_height(ui, Font::Label);
            let side = t.px(theme::CHECKBOX_SIZE).round();
            let summary_height = line.max(side);
            let summary_top = (field[1] + field[3] + gap).round();
            let label = "All stages##reference";
            let check_width = theme::text_width(ui, Font::Label, "All stages")
                + side
                + t.px(theme::CHECKBOX_GAP).round();
            let shown_label = (!self.all_stages).then(|| stage.label());
            let words = summary(
                shown_label,
                reference::count(&groups),
                reference::entries().len(),
                &self.query,
            );
            let room = (inner - check_width - gap).max(1.0);
            let words = theme::fit_text(ui, Font::Label, &words, room);
            let text_top = (summary_top + (summary_height - line) * 0.5).round();
            theme::with_font(ui, Font::Label, || {
                paint.text([x, text_top], theme::TEXT_QUIET, &words);
                ui.set_cursor_screen_pos([x + inner - check_width, summary_top]);
                if theme::checkbox(ui, label, &mut self.all_stages) {
                    self.highlight = None;
                }
            });

            let list_top = (summary_top + summary_height + gap).round();
            let list_height = (max[1] - stroke - pad[1] - list_top).max(1.0);
            ui.set_cursor_screen_pos([x, list_top]);
            let listed = theme::region(
                ui,
                "##reference-list",
                [0.0, 0.0],
                [inner, list_height],
                || self.draw_list(ui, &rows, stage),
            );
            if let Some(clicked) = listed.flatten()
                && action == Action::None
            {
                action = clicked;
            }

            let query_chars = u32::try_from(self.query.chars().count()).unwrap_or(u32::MAX);
            let shown = u32::try_from(reference::count(&groups)).unwrap_or(u32::MAX);
            let total = u32::try_from(reference::entries().len()).unwrap_or(u32::MAX);
            let searching = self.searching;
            theme::record(|record| {
                ReferenceSnapshot {
                    open: true,
                    searching,
                    stage: if self.all_stages { "" } else { stage.key() },
                    query_chars,
                    shown,
                    total,
                }
                .store(record);
            });
            ui.set_cursor_screen_pos(at);
            ui.dummy(size);
            action
        }

        /// The list's rows in view, inside its child window; what a click on one asks.
        fn draw_list(&mut self, ui: &Ui, rows: &[Row], stage: LayerStage) -> Option<Action> {
            let t = theme::current();
            let heading = t.px(REFERENCE_HEADING_HEIGHT).round();
            let entry = t.px(REFERENCE_ROW_HEIGHT).round();
            let (tops, total) = row_tops(rows, heading, entry);
            let origin = ui.cursor_screen_pos().map(f32::round);
            let view = ui.window_size()[1];
            if std::mem::take(&mut self.follow)
                && let Some(row) = self.highlight
                && let Some(&top) = tops.get(row)
            {
                ui.set_scroll_y(scroll_to(ui.scroll_y(), top, top + entry, view));
            }
            let scroll = ui.scroll_y();
            let width = ui.content_region_avail()[0].max(1.0);
            let paint = theme::painter(ui);
            let tag_width = theme::text_width(ui, Font::MonoSmall, WIDEST_TAG);
            let indent = t.px(REFERENCE_INDENT).round();
            let tag_gap = t.px(REFERENCE_TAG_GAP).round();
            let mut action = None;
            for row in rows_in_view(&tops, total, scroll, scroll + view) {
                let Some(&top) = tops.get(row) else {
                    continue;
                };
                let top = origin[1] + top;
                let height = super::row_height(&rows[row], heading, entry);
                ui.set_cursor_screen_pos([origin[0], top]);
                let clicked =
                    ui.invisible_button(format!("##reference-row-{row}"), [width, height]);
                let hovered = ui.is_item_hovered();
                let min = [origin[0], top];
                let end = [origin[0] + width, top + height];
                match &rows[row] {
                    Row::Heading {
                        category,
                        count,
                        open,
                    } => {
                        if clicked && !self.folded.remove(category) {
                            self.folded.insert(*category);
                        }
                        if hovered {
                            paint.fill(min, end, theme::ICON_HOVER_BG, 0.0);
                        }
                        draw_heading(ui, &paint, min, end, category.label(), *count, *open);
                    }
                    Row::Entry(item) => {
                        let chosen = self.highlight == Some(row);
                        let background = if chosen {
                            Some(theme::COMPLETION_SELECTED_BG)
                        } else if hovered {
                            Some(theme::TAB_HOVER_BG)
                        } else {
                            None
                        };
                        if let Some(color) = background {
                            paint.fill(min, end, color, 0.0);
                        }
                        let name_color = if chosen {
                            theme::COMPLETION_SELECTED_TEXT
                        } else if item.kind == ItemKind::Unavailable {
                            theme::ERROR_TEXT
                        } else if !item.stages.contains(stage) {
                            theme::TEXT_GUTTER
                        } else {
                            theme::COMPLETION_TEXT
                        };
                        let left = min[0] + indent;
                        let name_x = left + tag_width + tag_gap;
                        let note = stage_note(item, stage);
                        draw_entry(
                            ui,
                            &paint,
                            [left, name_x, end[0]],
                            [min[1], height],
                            item,
                            name_color,
                            note.as_deref(),
                        );
                        if hovered {
                            theme::tooltip(ui, &entry_tooltip(item, stage));
                        }
                        if clicked {
                            self.highlight = Some(row);
                            action = Some(activate(item));
                        }
                    }
                }
            }
            ui.set_cursor_screen_pos(origin);
            ui.dummy([width, total.max(1.0)]);
            action
        }
    }

    /// What a click on, or Enter at, `item` asks.
    fn activate(item: &'static Item) -> Action {
        if item.kind == ItemKind::MaskTerm {
            Action::Copy(item)
        } else if reference::writes(item) {
            Action::Write(item)
        } else {
            Action::None
        }
    }

    /// A heading's row from `min` to `end`: its chevron, its group in small capitals and its count
    /// at the right.
    fn draw_heading(
        ui: &Ui,
        paint: &theme::Painter<'_>,
        min: [f32; 2],
        end: [f32; 2],
        label: &str,
        count: usize,
        open: bool,
    ) {
        let t = theme::current();
        let half = t.px(REFERENCE_CHEVRON);
        let centre = [min[0] + 2.0 * half + t.px(2.0), (min[1] + end[1]) * 0.5];
        let points = if open {
            [
                [centre[0] - half, centre[1] - half * 0.5],
                [centre[0], centre[1] + half * 0.5],
                [centre[0] + half, centre[1] - half * 0.5],
            ]
        } else {
            [
                [centre[0] - half * 0.5, centre[1] - half],
                [centre[0] + half * 0.5, centre[1]],
                [centre[0] - half * 0.5, centre[1] + half],
            ]
        };
        paint.polyline(&points, theme::TEXT_QUIET, (1.5 * t.stroke()).max(1.0));
        let text_x = centre[0] + 2.0 * half + t.px(4.0);
        theme::with_font(ui, Font::Heading, || {
            let line = ui.text_line_height();
            let y = ((min[1] + end[1] - line) * 0.5).round();
            paint.text([text_x, y], theme::TEXT_QUIET, &label.to_uppercase());
        });
        theme::with_font(ui, Font::MonoSmall, || {
            let words = count.to_string();
            let line = ui.text_line_height();
            let y = ((min[1] + end[1] - line) * 0.5).round();
            let width = ui.calc_text_size(&words)[0];
            paint.text([end[0] - width - t.px(4.0), y], theme::TEXT_GUTTER, &words);
        });
    }

    /// An entry's row at `span[0]`, `span[1]` tall: its tag at `columns[0]`, its name at
    /// `columns[1]` and its first line of detail ending at `columns[2]`, or `note` there in its
    /// place: the stages an entry from another stage belongs to ([`super::stage_note`]).
    fn draw_entry(
        ui: &Ui,
        paint: &theme::Painter<'_>,
        columns: [f32; 3],
        span: [f32; 2],
        item: &Item,
        name_color: [f32; 4],
        note: Option<&str>,
    ) {
        let t = theme::current();
        let [top, height] = span;
        let [tag_x, name_x, right] = columns;
        let right = right - t.px(4.0);
        theme::with_font(ui, Font::MonoSmall, || {
            let y = (top + (height - ui.text_line_height()) * 0.5).round();
            paint.text([tag_x, y], theme::COMPLETION_QUIET, item.kind.tag());
        });
        theme::with_font(ui, Font::Mono, || {
            let y = (top + (height - ui.text_line_height()) * 0.5).round();
            let room = (right - name_x).max(1.0);
            let name = theme::fit_text(ui, Font::Mono, &item.name, room);
            paint.text([name_x, y], name_color, &name);
            let used = ui.calc_text_size(&name)[0];
            let detail = note.unwrap_or_else(|| item.detail.lines().next().unwrap_or_default());
            let gap = t.px(theme::COMPLETION_GAP);
            let left = room - used - gap;
            if left > t.px(24.0) && !detail.is_empty() && detail != item.name {
                let fitted = theme::fit_text(ui, Font::Mono, detail, left);
                let width = ui.calc_text_size(&fitted)[0];
                paint.text([right - width, y], theme::COMPLETION_QUIET, &fitted);
            }
        });
    }

    /// What hovering `item` shows: the reference's lines for it, and what a click does.
    fn entry_tooltip(item: &Item, stage: LayerStage) -> Vec<String> {
        let mut lines = reference::tooltip(item);
        let click = match item.kind {
            ItemKind::MaskTerm => {
                "A click copies it, for a layer's selector in the Selector tab.".to_owned()
            }
            ItemKind::Unavailable if item.detail.is_empty() => {
                "Not available in a layer; a click writes nothing.".to_owned()
            }
            ItemKind::Unavailable => format!("A click writes {} at the cursor.", item.detail),
            _ if !item.stages.contains(stage) => {
                format!("Not at {}: a click writes it all the same.", stage.label())
            }
            _ => "A click writes it at the cursor.".to_owned(),
        };
        lines.push(click);
        lines
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::LayerStage;
    use crate::editor::complete::Category;
    use crate::editor::reference;

    #[test]
    fn the_column_takes_its_share_between_its_bounds() {
        // The Code sub-tab is 720 wide less its padding: about 692 at 1080p.
        assert_eq!(column_width(692.0, 1.0), 290.0);
        assert_eq!(column_width(2000.0, 1.0), 300.0);
        assert_eq!(column_width(500.0, 1.0), 220.0);
        assert_eq!(column_width(300.0, 1.0), 150.0);
        assert_eq!(column_width(1384.0, 2.0), 581.0);
        assert!(column_width(f32::NAN, 1.0) >= 1.0);
        assert_eq!(column_width(0.0, 1.0), 1.0);
    }

    #[test]
    fn rows_stack_and_only_those_in_view_are_drawn() {
        let groups = reference::groups("", Some(LayerStage::TonemapPost));
        let rows = reference::rows(&groups, |c| c != Category::Context, false);
        let (tops, total) = row_tops(&rows, 24.0, 20.0);
        assert_eq!(tops[0], 0.0);
        assert_eq!(tops[1], 24.0);
        let headings = rows
            .iter()
            .filter(|row| matches!(row, Row::Heading { .. }))
            .count();
        let entries = rows.len() - headings;
        assert_eq!(total, 24.0 * headings as f32 + 20.0 * entries as f32);
        assert_eq!(rows_in_view(&tops, total, 0.0, 30.0), 0..2);
        assert_eq!(rows_in_view(&tops, total, 24.0, 44.0), 1..2);
        assert_eq!(rows_in_view(&tops, total, 23.9, 24.1), 0..2);
        let all = rows_in_view(&tops, total, 0.0, total);
        assert_eq!(all, 0..rows.len());
        assert_eq!(
            rows_in_view(&tops, total, total, total + 10.0),
            rows.len()..rows.len()
        );
        assert_eq!(rows_in_view(&[], 0.0, 0.0, 100.0), 0..0);
    }

    #[test]
    fn up_and_down_step_through_entries_round_the_ends() {
        let entries = [1, 2, 5];
        assert_eq!(step(&entries, None, true), Some(1));
        assert_eq!(step(&entries, None, false), Some(5));
        assert_eq!(step(&entries, Some(2), true), Some(5));
        assert_eq!(step(&entries, Some(5), true), Some(1));
        assert_eq!(step(&entries, Some(1), false), Some(5));
        assert_eq!(step(&entries, Some(3), true), Some(1));
        assert_eq!(step(&[], Some(1), true), None);
    }

    #[test]
    fn the_highlight_is_scrolled_into_view_by_the_least() {
        assert_eq!(scroll_to(100.0, 50.0, 70.0, 200.0), 50.0);
        assert_eq!(scroll_to(100.0, 290.0, 310.0, 200.0), 110.0);
        assert_eq!(scroll_to(100.0, 150.0, 170.0, 200.0), 100.0);
        assert_eq!(scroll_to(0.0, 10.0, 300.0, 200.0), 100.0);
    }

    #[test]
    fn an_entry_from_another_stage_says_which_stages_it_belongs_to() {
        let named = |name: &str| {
            reference::entries()
                .iter()
                .copied()
                .find(|item| item.name == name)
                .unwrap_or_else(|| panic!("`{name}` is not listed"))
        };
        let wrap = named("er_wrap_time");
        assert_eq!(
            stage_note(wrap, LayerStage::TonemapPost).as_deref(),
            Some("Vertex")
        );
        assert_eq!(stage_note(wrap, LayerStage::Vertex), None);
        assert_eq!(
            stage_note(named("ddx"), LayerStage::Vertex).as_deref(),
            Some("every pixel stage")
        );
        assert_eq!(stage_note(named("lerp"), LayerStage::Vertex), None);
        assert_eq!(stage_note(named("tex2D"), LayerStage::Vertex), None);
    }

    #[test]
    fn the_summary_says_where_and_how_many() {
        assert_eq!(
            summary(Some("Tonemap post"), 286, 405, ""),
            "Tonemap post: 286 entries"
        );
        assert_eq!(summary(None, 405, 405, "  "), "Every stage: 405 entries");
        assert_eq!(
            summary(Some("Albedo"), 3, 405, " time "),
            "Albedo: 3 of 405 match \"time\""
        );
    }
}
