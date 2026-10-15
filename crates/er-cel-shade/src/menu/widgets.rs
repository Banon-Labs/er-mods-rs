//! A layer parameter's widget (design 9), chosen from its annotations
//! ([`crate::editor::annotations`]).
//!
//! | Uniform | Widget |
//! |---|---|
//! | `float` | a slider with the format and units, logarithmic for `ui_scale = "log"` |
//! | `int`, `uint` | a slider, or a combo when `ui_items` is set |
//! | `bool` | a checkbox |
//! | `float3`/`float4` with `ui_type = "color"` | a colour editor |
//! | other vectors, `ui_type = "drag"` | a drag per component |
//!
//! Right-click on any of them gives Reset and Copy value.
//!
//! # The row
//!
//! Each parameter is one row as the Main mockup draws it: the label in a column of its own,
//! 70 pixels at 1080p or as wide as the longest label needs ([`MAX_LABEL_SHARE`] of the row at
//! most), then the control. A slider is a bare 22-pixel track with the value in 12-pixel mono at
//! the row's end ([`value_label`]); a colour is a 22-pixel swatch, which opens a picker, and a
//! mono field per channel behind its coloured letter; a combo is 140 pixels wide. The whole row
//! takes the tooltip and the right-click menu.
//!
//! A slider draws no text of its own: its format starts with `##`, which Dear ImGui does not draw
//! but still rounds a drag by and fills the Ctrl+click field through
//! ([`super::theme::slider_format`]), at the decimals [`value_label`] shows. [`rounded`] rounds a
//! typed value the same way, which Dear ImGui leaves as typed. An integer slider is a float
//! slider kept to whole numbers, so its grab is the mockup's 12 pixels: Dear ImGui widens an
//! integer slider's grab to one step of its track.
//!
//! # What Dear ImGui refuses
//!
//! A slider whose ends pass half its type's range trips an assertion inside Dear ImGui, and an
//! assertion in this build is a C `assert`, which ends the game. A parameter's range comes from
//! the user's file, so [`control`] gives a slider only a range it is sure of -- both ends
//! present, finite, inside [`FLOAT_SLIDER_LIMIT`] or [`INT_SLIDER_LIMIT`] -- and anything else a
//! drag, which has no such check. Units are printed through the widget's printf format, so a `%`
//! in them is doubled.
//!
//! The choice is pure and tested on the host; the drawing is the Windows half.

use crate::editor::annotations::{ParamDecl, ParamType, Widget};
use crate::editor::project::ParamValue;

/// The widest range a float slider is given. Dear ImGui's own bound is half of `f64`'s range.
pub const FLOAT_SLIDER_LIMIT: f64 = 1.0e30;
/// The widest range an integer slider is given: Dear ImGui's bound, half of `i32`'s range.
pub const INT_SLIDER_LIMIT: f64 = (i32::MAX / 2) as f64;
/// Decimals shown when the annotations give no `ui_step`.
pub const DEFAULT_DECIMALS: usize = 3;
/// The most decimals a step is shown to.
pub const MAX_DECIMALS: usize = 6;
/// Pixels a drag moves to sweep its whole range, when it has one.
pub const DRAG_SWEEP_PIXELS: f64 = 200.0;
/// A drag's change per pixel when it has neither a step nor a range.
pub const DEFAULT_DRAG_SPEED: f64 = 0.01;
/// The most of a row the label column takes, however long a label is.
pub const MAX_LABEL_SHARE: f32 = 0.4;
/// A combo's width, a colour's swatch and a channel's field, at 1080p (Main mockup).
pub const COMBO_WIDTH: f32 = 140.0;
pub const SWATCH: f32 = 22.0;
pub const CHANNEL_FIELD: f32 = 56.0;
/// The gap between a channel's letter and its field.
pub const CHANNEL_LETTER_GAP: f32 = 3.0;
/// The narrowest a slider or a channel's field is drawn, at 1080p.
pub const MIN_CONTROL: f32 = 40.0;
/// The letters in front of a colour's fields.
pub const CHANNELS: [&str; 4] = ["R", "G", "B", "A"];

/// The control a parameter is drawn with, with everything Dear ImGui is handed.
#[derive(Clone, Debug, PartialEq)]
pub enum Control {
    /// A float slider over `min..=max`.
    Slider {
        min: f64,
        max: f64,
        format: String,
        logarithmic: bool,
    },
    /// An integer slider over `min..=max`.
    IntSlider {
        min: i32,
        max: i32,
        format: String,
    },
    /// One of `items`; the value is the item's index.
    Combo {
        items: Vec<String>,
    },
    Checkbox,
    /// A colour editor showing floats; `hdr` lets a component pass 1.
    Color {
        alpha: bool,
        hdr: bool,
    },
    /// A drag per component, clamped to `range` when there is one.
    Drag {
        components: usize,
        speed: f64,
        range: Option<(f64, f64)>,
        format: String,
    },
}

/// The control for `decl`.
#[must_use]
pub fn control(decl: &ParamDecl) -> Control {
    if decl.ty == ParamType::Bool {
        return Control::Checkbox;
    }
    match decl.widget {
        Widget::Combo if decl.ty.integer() && !decl.items.is_empty() => Control::Combo {
            items: decl.items.clone(),
        },
        Widget::Color if matches!(decl.ty, ParamType::Float3 | ParamType::Float4) => {
            Control::Color {
                alpha: decl.ty == ParamType::Float4,
                hdr: decl.max.is_none_or(|max| max > 1.0),
            }
        }
        Widget::Slider if decl.ty == ParamType::Float => {
            match range_within(decl, FLOAT_SLIDER_LIMIT) {
                Some((min, max)) => Control::Slider {
                    min,
                    max,
                    format: number_format(decl, false),
                    logarithmic: decl.log_scale && min > 0.0,
                },
                None => drag(decl),
            }
        }
        Widget::Slider if decl.ty.integer() => {
            let whole = range_within(decl, INT_SLIDER_LIMIT)
                .map(|(min, max)| (min.ceil(), max.floor()))
                .filter(|(min, max)| min < max);
            match whole {
                // Inside `INT_SLIDER_LIMIT`, so the casts are exact.
                Some((min, max)) => Control::IntSlider {
                    min: min as i32,
                    max: max as i32,
                    format: number_format(decl, true),
                },
                None => drag(decl),
            }
        }
        _ => drag(decl),
    }
}

/// `decl`'s range when both ends are given, finite, between `-limit` and `limit`, and apart: a
/// slider over a single value has nothing to slide.
fn range_within(decl: &ParamDecl, limit: f64) -> Option<(f64, f64)> {
    let (min, max) = (decl.min?, decl.max?);
    (min.is_finite() && max.is_finite() && min >= -limit && max <= limit && min < max)
        .then_some((min, max))
}

fn drag(decl: &ParamDecl) -> Control {
    let range = range_within(decl, FLOAT_SLIDER_LIMIT);
    let speed = decl
        .step
        .filter(|step| step.is_finite() && *step > 0.0)
        .or_else(|| range.map(|(min, max)| (max - min) / DRAG_SWEEP_PIXELS))
        .unwrap_or(if decl.ty.integer() {
            1.0
        } else {
            DEFAULT_DRAG_SPEED
        });
    Control::Drag {
        components: decl.ty.components(),
        speed,
        range,
        format: number_format(decl, false),
    }
}

/// The fewest decimals, up to [`MAX_DECIMALS`], that show `step` exactly.
#[must_use]
pub fn decimals_for(step: f64) -> usize {
    if !step.is_finite() || step <= 0.0 {
        return DEFAULT_DECIMALS;
    }
    (0..=MAX_DECIMALS)
        .find(|&decimals| {
            let scaled = step * 10_f64.powi(decimals as i32);
            (scaled - scaled.round()).abs() < 1e-6 * scaled.max(1.0)
        })
        .unwrap_or(MAX_DECIMALS)
}

/// The printf format a widget shows `decl`'s value with: `%d` for an integer slider (`int_slider`),
/// otherwise as many decimals as its step needs (none for a whole-number type), then its units
/// with every `%` doubled.
#[must_use]
pub fn number_format(decl: &ParamDecl, int_slider: bool) -> String {
    let mut format = if int_slider {
        "%d".to_owned()
    } else if decl.ty.integer() {
        "%.0f".to_owned()
    } else {
        let decimals = decl.step.map_or(DEFAULT_DECIMALS, decimals_for);
        format!("%.{decimals}f")
    };
    let units = decl.units.trim();
    if !units.is_empty() {
        format.push(' ');
        format.push_str(&units.replace('%', "%%"));
    }
    format
}

/// The decimals a slider's value is shown and kept to: none for a whole-number type, else as
/// many as its step needs.
fn decimals_shown(decl: &ParamDecl) -> usize {
    if decl.ty.integer() {
        0
    } else {
        decl.step.map_or(DEFAULT_DECIMALS, decimals_for)
    }
}

/// The words at the end of a slider's row for `value` (Main mockup's `0.60`): the decimals
/// [`number_format`] would print, then the units as they are written.
#[must_use]
pub fn value_label(decl: &ParamDecl, value: f64) -> String {
    let decimals = decimals_shown(decl);
    let mut text = format!("{:.decimals$}", rounded(decl, value));
    let units = decl.units.trim();
    if !units.is_empty() {
        text.push(' ');
        text.push_str(units);
    }
    text
}

/// `value` rounded to what [`value_label`] shows, as Dear ImGui rounds a slider to its format.
/// Never `-0`, which would print as `-0.00`.
#[must_use]
pub fn rounded(decl: &ParamDecl, value: f64) -> f64 {
    let scale = 10_f64.powi(decimals_shown(decl) as i32);
    (value * scale).round() / scale + 0.0
}

/// `values` as the project file writes a parameter: `true`, `0.6`, `[0.0, 0.02, 0.05]`. What
/// Copy value puts on the clipboard, so it pastes into `project.ershade` as it is.
#[must_use]
pub fn value_text(decl: &ParamDecl, values: &[f64; 4]) -> String {
    let count = decl.ty.components();
    match decl.ty {
        ParamType::Bool => (values[0] != 0.0).to_string(),
        ParamType::Int | ParamType::Uint => format!("{}", values[0].round() as i64),
        ParamType::Float => format!("{:?}", values[0]),
        ParamType::Float2 | ParamType::Float3 | ParamType::Float4 => {
            let parts: Vec<String> = values[..count].iter().map(|v| format!("{v:?}")).collect();
            format!("[{}]", parts.join(", "))
        }
    }
}

/// `values` as the project file's value for `decl`, after [`ParamDecl::clamp`].
#[must_use]
pub fn param_value(decl: &ParamDecl, values: [f64; 4]) -> ParamValue {
    let values = decl.clamp(values);
    match decl.ty {
        ParamType::Bool => ParamValue::Bool(values[0] != 0.0),
        ParamType::Float | ParamType::Int | ParamType::Uint => ParamValue::Number(values[0]),
        ParamType::Float2 | ParamType::Float3 | ParamType::Float4 => {
            ParamValue::Vector(values[..decl.ty.components()].to_vec())
        }
    }
}

/// The parameters to show, grouped by `ui_category` (design 9's "LIFT", "GENERAL"): each
/// category in the order it first appears, then the parameters with none, whose group has no
/// name. Hidden parameters are left out.
#[must_use]
pub fn categories(decls: &[ParamDecl]) -> Vec<(Option<&str>, Vec<&ParamDecl>)> {
    let mut groups: Vec<(Option<&str>, Vec<&ParamDecl>)> = Vec::new();
    let mut general: Vec<&ParamDecl> = Vec::new();
    for decl in decls.iter().filter(|decl| !decl.hidden) {
        let category = decl.category.trim();
        if category.is_empty() {
            general.push(decl);
            continue;
        }
        match groups.iter_mut().find(|(name, _)| *name == Some(category)) {
            Some((_, members)) => members.push(decl),
            None => groups.push((Some(category), vec![decl])),
        }
    }
    if !general.is_empty() {
        groups.push((None, general));
    }
    groups
}

#[cfg(windows)]
pub(super) use process::{Edit, draw, label_width};

/// Drawing one parameter.
#[cfg(windows)]
mod process {
    use hudhook::imgui::{ColorEditFlags, Drag, SliderFlags, Ui};

    use super::super::theme::{self, Font, SliderRow, fit_text};
    use super::{
        CHANNEL_FIELD, CHANNEL_LETTER_GAP, CHANNELS, COMBO_WIDTH, Control, MAX_LABEL_SHARE,
        MIN_CONTROL, SWATCH, control, decimals_shown, param_value, rounded, value_label,
        value_text,
    };
    use crate::editor::annotations::ParamDecl;
    use crate::editor::param_values;
    use crate::editor::project::ParamValue;

    /// What the player did to a parameter this frame.
    pub(in crate::menu) enum Edit {
        None,
        /// A new value, already clamped.
        Set(ParamValue),
        /// Back to the annotated default: the project forgets its value.
        Reset,
    }

    fn round2(point: [f32; 2]) -> [f32; 2] {
        [point[0].round(), point[1].round()]
    }

    /// The label column for `decls` in a row `avail` screen pixels wide: the Main mockup's 70
    /// pixels, or the longest label, up to [`MAX_LABEL_SHARE`] of the row.
    pub(in crate::menu) fn label_width(ui: &Ui, decls: &[&ParamDecl], avail: f32) -> f32 {
        let floor = theme::px(SliderRow::PARAMETER.label_width).round();
        let widest = decls
            .iter()
            .map(|decl| theme::text_width(ui, Font::Body, &decl.label).ceil())
            .fold(0.0, f32::max);
        widest
            .max(floor)
            .min((avail * MAX_LABEL_SHARE).round().max(floor))
    }

    /// One parameter's row at the cursor: its label in secondary 13-pixel words, vertically
    /// centred in the label column, and the control after it.
    struct ParamRow {
        at: [f32; 2],
        width: f32,
        height: f32,
        /// Where the control column starts.
        control: f32,
        gap: f32,
    }

    impl ParamRow {
        /// A row `height` (1080p) tall with `label` in a column `label_width` screen pixels wide.
        fn begin(ui: &Ui, label: &str, label_width: f32, height: f32) -> Self {
            let t = theme::current();
            let at = round2(ui.cursor_screen_pos());
            let width = ui.content_region_avail()[0].max(1.0);
            let height = t.px(height).round();
            let gap = t.px(SliderRow::PARAMETER.gap).round();
            let words = fit_text(ui, Font::Body, label, label_width);
            let line = ui.text_line_height();
            theme::painter(ui).text(
                [at[0], at[1] + (height - line) * 0.5],
                SliderRow::PARAMETER.label_colour,
                &words,
            );
            Self {
                at,
                width,
                height,
                control: at[0] + label_width + gap,
                gap,
            }
        }

        /// The row's right end.
        fn right(&self) -> f32 {
            self.at[0] + self.width
        }

        /// Put the cursor `x` into the control column, for an item `height` screen pixels tall
        /// centred in the row; returns its top-left.
        fn place(&self, ui: &Ui, x: f32, height: f32) -> [f32; 2] {
            let at = [
                (self.control + x).round(),
                self.at[1] + ((self.height - height) * 0.5).round(),
            ];
            ui.set_cursor_screen_pos(at);
            at
        }

        /// The whole row as one item, so the tooltip and the right-click menu answer anywhere on it.
        fn end(self, ui: &Ui) {
            ui.set_cursor_screen_pos(self.at);
            ui.dummy([self.width, self.height.max(1.0)]);
        }
    }

    /// The slider of `decl`'s row: a bare 22-pixel track filling the control column, kept to the
    /// places its value is shown to, and that value ([`value_label`]) right-aligned in 12-pixel
    /// mono at the row's end, at least 36 pixels wide.
    fn slider(
        ui: &Ui,
        row: &ParamRow,
        decl: &ParamDecl,
        range: (f64, f64),
        flags: SliderFlags,
        value: &mut f64,
    ) -> bool {
        let t = theme::current();
        let text = value_label(decl, *value);
        let text = text.as_str();
        let paint = theme::painter(ui);
        let words = theme::with_font(ui, Font::Mono, || {
            let size = ui.calc_text_size(text);
            paint.text(
                [
                    row.right() - size[0],
                    row.at[1] + (row.height - size[1]) * 0.5,
                ],
                theme::TEXT,
                text,
            );
            size[0]
        });
        let value_width = t
            .px(SliderRow::PARAMETER.value_width)
            .round()
            .max(words.ceil());
        let width = (row.right() - value_width - row.gap - row.control).max(t.px(MIN_CONTROL));
        row.place(ui, 0.0, row.height);
        ui.set_next_item_width(width);
        let _padding = theme::frame(ui, theme::SLIDER_HEIGHT, theme::FRAME_PAD_X);
        ui.slider_config(format!("##{}", decl.name), range.0, range.1)
            .display_format(theme::slider_format(decimals_shown(decl)))
            .flags(flags)
            .build(value)
    }

    /// A colour's control: the swatch, which opens a picker, then a mono field per channel behind
    /// its coloured letter (Main mockup's `Shadows`).
    fn colour(ui: &Ui, row: &ParamRow, values: &mut [f64; 4], alpha: bool, hdr: bool) -> bool {
        let t = theme::current();
        let count = if alpha { 4 } else { 3 };
        let side = t.px(SWATCH).round();
        let at = row.place(ui, 0.0, side);
        let open = ui.invisible_button("##swatch", [side, side]);
        if ui.is_item_hovered() {
            theme::tooltip(ui, &["Click for a colour picker."]);
        }
        let shown = |at: usize| values[at].clamp(0.0, 1.0) as f32;
        let swatch = [
            shown(0),
            shown(1),
            shown(2),
            if alpha { shown(3) } else { 1.0 },
        ];
        let paint = theme::painter(ui);
        let radius = t.px(theme::FRAME_RADIUS);
        paint.fill(at, [at[0] + side, at[1] + side], swatch, radius);
        paint.outline(
            at,
            [at[0] + side, at[1] + side],
            theme::BUTTON_BORDER,
            radius,
        );
        if open {
            ui.open_popup("##picker");
        }

        let letters: Vec<f32> = CHANNELS[..count]
            .iter()
            .map(|letter| theme::text_width(ui, Font::Small, letter).ceil())
            .collect();
        let letter_gap = t.px(CHANNEL_LETTER_GAP).round();
        let fixed = side + letters.iter().sum::<f32>() + count as f32 * (row.gap + letter_gap);
        let room = (row.right() - row.control - fixed) / count as f32;
        let field = t.px(CHANNEL_FIELD).min(room).max(t.px(MIN_CONTROL)).floor();
        let frame = t.px(theme::FRAME_HEIGHT).round();
        let tints = [
            theme::CHANNEL_R,
            theme::CHANNEL_G,
            theme::CHANNEL_B,
            theme::TEXT_QUIET,
        ];
        let mut changed = false;
        let mut x = side + row.gap;
        for (channel, (letter, tint)) in letters.iter().zip(tints).enumerate() {
            theme::with_font(ui, Font::Small, || {
                let line = ui.text_line_height();
                paint.text(
                    [row.control + x, row.at[1] + (row.height - line) * 0.5],
                    tint,
                    CHANNELS[channel],
                );
            });
            x += letter + letter_gap;
            row.place(ui, x, frame);
            ui.set_next_item_width(field);
            let _mono = theme::font(ui, Font::Mono);
            let _padding = theme::frame(ui, theme::FRAME_HEIGHT, theme::MONO_FRAME_PAD_X);
            changed |= ui
                .input_scalar(format!("##{}", CHANNELS[channel]), &mut values[channel])
                .display_format("%.3f")
                .build();
            x += field + row.gap;
        }

        if let Some(_picker) = ui.begin_popup("##picker") {
            let mut flags = ColorEditFlags::FLOAT;
            if hdr {
                flags |= ColorEditFlags::HDR;
            }
            if alpha {
                let mut picked = [0.0_f32; 4];
                for (slot, value) in picked.iter_mut().zip(*values) {
                    *slot = value as f32;
                }
                if ui
                    .color_picker4_config("##picker-colour", &mut picked)
                    .flags(flags | ColorEditFlags::ALPHA_BAR)
                    .build()
                {
                    for (slot, value) in values.iter_mut().zip(picked) {
                        *slot = f64::from(value);
                    }
                    changed = true;
                }
            } else {
                let mut picked = [values[0] as f32, values[1] as f32, values[2] as f32];
                if ui
                    .color_picker3_config("##picker-colour", &mut picked)
                    .flags(flags)
                    .build()
                {
                    for (slot, value) in values.iter_mut().zip(picked) {
                        *slot = f64::from(value);
                    }
                    changed = true;
                }
            }
        }
        changed
    }

    /// Draw `decl`'s row for the project's `value`, its label in a column `label_width` screen
    /// pixels wide ([`label_width`]), and say what changed.
    pub(in crate::menu) fn draw(
        ui: &Ui,
        decl: &ParamDecl,
        value: Option<&ParamValue>,
        label_width: f32,
    ) -> Edit {
        let t = theme::current();
        let current = param_values(decl, value).unwrap_or(decl.default);
        let mut values = current;
        let id = format!("##{}", decl.name);
        let frame = t.px(theme::FRAME_HEIGHT).round();
        let control = control(decl);
        let height = match control {
            Control::Slider { .. } | Control::IntSlider { .. } => theme::SLIDER_HEIGHT,
            _ => theme::FRAME_HEIGHT,
        };
        let row = ParamRow::begin(ui, &decl.label, label_width, height);
        let changed = match control {
            Control::Slider {
                min,
                max,
                logarithmic,
                ..
            } => {
                let mut flags = SliderFlags::ALWAYS_CLAMP;
                if logarithmic {
                    flags |= SliderFlags::LOGARITHMIC;
                }
                let moved = slider(ui, &row, decl, (min, max), flags, &mut values[0]);
                if moved {
                    values[0] = rounded(decl, values[0]);
                }
                moved
            }
            Control::IntSlider { min, max, .. } => {
                let (min, max) = (f64::from(min), f64::from(max));
                values[0] = values[0].round().clamp(min, max);
                let flags = SliderFlags::ALWAYS_CLAMP;
                let moved = slider(ui, &row, decl, (min, max), flags, &mut values[0]);
                values[0] = values[0].round();
                moved
            }
            Control::Combo { items } => {
                let last = items.len().saturating_sub(1);
                let mut index = (values[0].round().max(0.0) as usize).min(last);
                let mut picked = false;
                let preview = items.get(index).map_or("", String::as_str);
                let pad = 2.0 * t.px(theme::FRAME_PAD_X) + frame;
                let widest = items
                    .iter()
                    .map(|item| ui.calc_text_size(item)[0] + pad)
                    .fold(t.px(COMBO_WIDTH), f32::max);
                let width = widest.min(row.right() - row.control).round();
                row.place(ui, 0.0, frame);
                if let Some(_combo) = theme::combo(ui, &id, preview, width) {
                    for (at, item) in items.iter().enumerate() {
                        if ui
                            .selectable_config(format!("{item}##{at}"))
                            .selected(at == index)
                            .build()
                        {
                            index = at;
                            picked = true;
                        }
                    }
                }
                values[0] = index as f64;
                picked
            }
            Control::Checkbox => {
                let mut on = values[0] != 0.0;
                row.place(ui, 0.0, ui.text_line_height());
                let toggled = theme::checkbox(ui, &id, &mut on);
                values[0] = f64::from(u8::from(on));
                toggled
            }
            Control::Color { alpha, hdr } => colour(ui, &row, &mut values, alpha, hdr),
            Control::Drag {
                components,
                speed,
                range,
                format,
            } => {
                row.place(ui, 0.0, frame);
                ui.set_next_item_width((row.right() - row.control).max(t.px(MIN_CONTROL)));
                let _mono = theme::font(ui, Font::Mono);
                let _padding = theme::frame(ui, theme::FRAME_HEIGHT, theme::MONO_FRAME_PAD_X);
                let mut drag = Drag::new(&id).speed(speed as f32).display_format(&format);
                if let Some((min, max)) = range {
                    drag = drag.range(min, max);
                }
                let count = components.clamp(1, 4);
                drag.build_array(ui, &mut values[..count])
            }
        };
        row.end(ui);
        if ui.is_item_hovered() && !decl.tooltip.is_empty() {
            theme::tooltip(ui, &[decl.tooltip.as_str()]);
        }
        let mut edit = if changed {
            Edit::Set(param_value(decl, values))
        } else {
            Edit::None
        };
        // Named, not the last item's id: the row ends in a dummy, whose id is 0, and Dear ImGui
        // asserts on a context menu with no id.
        if let Some(_popup) = ui.begin_popup_context_with_label("##value-menu") {
            if ui.selectable(format!(
                "Reset to {}",
                value_text(decl, &decl.clamp(decl.default))
            )) {
                edit = Edit::Reset;
            }
            if ui.selectable("Copy value") {
                ui.set_clipboard_text(value_text(decl, &current));
            }
        }
        edit
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::annotations;

    fn decl(source: &str) -> ParamDecl {
        let annotated = annotations::parse(source, 0);
        assert!(!annotated.has_errors(), "{:?}", annotated.problems);
        annotated.params[0].clone()
    }

    #[test]
    fn a_float_is_a_slider_with_its_format_and_units() {
        let strength = decl(
            "uniform float Fog < ui_min = 0; ui_max = 30; ui_step = 0.25; ui_units = \"m\"; > = 2;",
        );
        assert_eq!(
            control(&strength),
            Control::Slider {
                min: 0.0,
                max: 30.0,
                format: "%.2f m".to_owned(),
                logarithmic: false,
            }
        );
        // No step: three decimals. A `%` in the units is doubled for printf.
        let percent = decl("uniform float P < ui_min = 0; ui_max = 100; ui_units = \"%\"; >;");
        assert_eq!(
            control(&percent),
            Control::Slider {
                min: 0.0,
                max: 100.0,
                format: "%.3f %%".to_owned(),
                logarithmic: false,
            }
        );
        let log = decl("uniform float Gain < ui_min = 0.01; ui_max = 10; ui_scale = \"log\"; >;");
        assert!(matches!(
            control(&log),
            Control::Slider {
                logarithmic: true,
                ..
            }
        ));
    }

    #[test]
    fn a_float_without_a_safe_range_is_a_drag() {
        // No range at all: a drag at the default speed.
        let free = decl("uniform float Offset = 0.5;");
        assert_eq!(
            control(&free),
            Control::Drag {
                components: 1,
                speed: DEFAULT_DRAG_SPEED,
                range: None,
                format: "%.3f".to_owned(),
            }
        );
        // One end only, and a range past what Dear ImGui accepts in a slider.
        for source in [
            "uniform float A < ui_min = 0; >;",
            "uniform float A < ui_min = -1e35; ui_max = 1e35; >;",
        ] {
            assert!(
                matches!(control(&decl(source)), Control::Drag { components: 1, .. }),
                "{source}"
            );
        }
    }

    #[test]
    fn an_int_is_a_slider_or_a_combo() {
        let mode =
            decl("uniform int Mode < ui_type = \"combo\"; ui_items = \"Soft\\0Hard\\0\"; >;");
        assert_eq!(
            control(&mode),
            Control::Combo {
                items: vec!["Soft".to_owned(), "Hard".to_owned()],
            }
        );
        let bands =
            decl("uniform int Bands < ui_min = 1.5; ui_max = 8; ui_units = \"bands\"; > = 4;");
        assert_eq!(
            control(&bands),
            Control::IntSlider {
                min: 2,
                max: 8,
                format: "%d bands".to_owned(),
            }
        );
        // A range Dear ImGui would refuse in an integer slider, and none at all: drags of whole
        // numbers.
        for source in [
            "uniform uint Seed < ui_min = 0; ui_max = 4000000000; >;",
            "uniform int Count = 3;",
        ] {
            let control = control(&decl(source));
            assert!(
                matches!(&control, Control::Drag { components: 1, format, .. } if format == "%.0f"),
                "{source}: {control:?}"
            );
        }
    }

    #[test]
    fn a_bool_is_a_checkbox_and_a_colour_is_a_colour_editor() {
        assert_eq!(control(&decl("uniform bool On = true;")), Control::Checkbox);
        let shadows =
            decl("uniform float3 Shadows < ui_type = \"color\"; > = float3(0, 0.02, 0.05);");
        assert_eq!(
            control(&shadows),
            Control::Color {
                alpha: false,
                hdr: true
            }
        );
        let tint = decl(
            "uniform float4 Tint < ui_type = \"color\"; ui_min = 0; ui_max = 1; > = float4(1, 1, 1, 1);",
        );
        assert_eq!(
            control(&tint),
            Control::Color {
                alpha: true,
                hdr: false
            }
        );
    }

    #[test]
    fn other_vectors_are_a_drag_per_component() {
        let corner = decl("uniform float2 Corner < ui_min = 0; ui_max = 1; > = float2(0.5, 1);");
        assert_eq!(
            control(&corner),
            Control::Drag {
                components: 2,
                speed: 1.0 / DRAG_SWEEP_PIXELS,
                range: Some((0.0, 1.0)),
                format: "%.3f".to_owned(),
            }
        );
        let wind = decl("uniform float3 Wind < ui_step = 0.1; > = 0;");
        assert!(matches!(
            control(&wind),
            Control::Drag {
                components: 3,
                range: None,
                ..
            }
        ));
        // A colour asked of a float2 is a drag too.
        let not_colour = decl("uniform float2 Two < ui_type = \"color\"; >;");
        assert!(matches!(
            control(&not_colour),
            Control::Drag { components: 2, .. }
        ));
    }

    #[test]
    fn a_step_decides_the_decimals() {
        for (step, decimals) in [(1.0, 0), (0.5, 1), (0.25, 2), (0.01, 2), (0.001, 3)] {
            assert_eq!(decimals_for(step), decimals, "{step}");
        }
        assert_eq!(decimals_for(0.0), DEFAULT_DECIMALS);
        assert_eq!(decimals_for(f64::NAN), DEFAULT_DECIMALS);
        assert_eq!(decimals_for(1.0 / 3.0), MAX_DECIMALS);
    }

    #[test]
    fn copy_value_writes_what_the_project_file_reads() {
        let shadows = decl("uniform float3 Shadows = float3(0, 0.02, 0.05);");
        assert_eq!(value_text(&shadows, &shadows.default), "[0.0, 0.02, 0.05]");
        let mode = decl("uniform int Mode = 2;");
        assert_eq!(value_text(&mode, &mode.default), "2");
        let on = decl("uniform bool On = true;");
        assert_eq!(value_text(&on, &on.default), "true");
        let strength = decl("uniform float Strength < ui_min = 0; ui_max = 1; > = 0.6;");
        assert_eq!(value_text(&strength, &strength.default), "0.6");
        // And a value set through a widget is clamped and shaped as the project file holds it.
        assert_eq!(
            param_value(&strength, [4.0, 0.0, 0.0, 0.0]),
            ParamValue::Number(1.0)
        );
        assert_eq!(
            param_value(&shadows, [0.1, 0.2, 0.3, 9.0]),
            ParamValue::Vector(vec![0.1, 0.2, 0.3])
        );
        assert_eq!(
            param_value(&on, [0.0, 0.0, 0.0, 0.0]),
            ParamValue::Bool(false)
        );
    }

    #[test]
    fn parameters_are_grouped_by_category_with_the_rest_last() {
        let annotated = annotations::parse(
            "uniform float Strength < ui_min = 0; ui_max = 1; > = 0.6;\n\
             uniform float3 Shadows < ui_type = \"color\"; ui_category = \"Lift\"; > = 0;\n\
             uniform int Mode < ui_type = \"combo\"; ui_items = \"Soft\\0Hard\\0\"; > = 0;\n\
             uniform float Gamma < ui_category = \"Lift\"; > = 1;\n\
             uniform float Secret < hidden = true; > = 1;\n",
            0,
        );
        let groups = categories(&annotated.params);
        let names: Vec<(Option<&str>, Vec<&str>)> = groups
            .iter()
            .map(|(category, decls)| (*category, decls.iter().map(|d| d.name.as_str()).collect()))
            .collect();
        assert_eq!(
            names,
            vec![
                (Some("Lift"), vec!["Shadows", "Gamma"]),
                (None, vec!["Strength", "Mode"]),
            ]
        );
        assert!(categories(&[]).is_empty());
    }

    #[test]
    fn a_sliders_value_is_shown_and_kept_to_its_decimals() {
        let strength =
            decl("uniform float Strength < ui_min = 0; ui_max = 1; ui_step = 0.01; > = 0.6;");
        assert_eq!(value_label(&strength, 0.6034), "0.60");
        assert_eq!(rounded(&strength, 0.6034), 0.6);
        // No step: three decimals. Units follow as written, a `%` not doubled.
        let fog = decl("uniform float Fog < ui_min = 0; ui_max = 30; ui_units = \"%\"; > = 2;");
        assert_eq!(value_label(&fog, 2.0), "2.000 %");
        let bands = decl("uniform int Bands < ui_min = 1; ui_max = 8; ui_units = \"bands\"; >;");
        assert_eq!(value_label(&bands, 3.6), "4 bands");
        assert_eq!(rounded(&bands, 3.6), 4.0);
        // A value a hair under zero shows as zero, not `-0.00`.
        assert_eq!(value_label(&strength, -0.001), "0.00");
        assert!(rounded(&strength, -0.001).is_sign_positive());
    }
}
