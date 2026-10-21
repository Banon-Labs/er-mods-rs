//! The Look tab (design 9 and 17): the cel look and its presets. The preset combo, Save and
//! Revert sit in the panel's toolbar; what is left here is cel on or off, the light bands' and
//! outlines' knobs, which the AO port still reads, the stock cel layers that hold the rest of the
//! look, saving the look as a preset, the menu key, and reading `er-cel-shade.toml` again.
//!
//! The surfaces, saturation and brightness steps are layers in the Layers tab now
//! (`crate::editor::cel`), so this tab lists them with their values and a button to show each in
//! the Layers tab, rather than drawing a second set of sliders for the same parameters. A preset
//! sets them, and "Cel shading on" turns them on and off with the knobs.
//!
//! The canvas has no mockup of this tab, so it is set in the language of the others: blocks 10
//! apart and sections 14, quiet uppercase headings, the Parameters mockup's slider rows (the label
//! in a column, a gold grab, the value right-aligned in mono), gold for the action a section is
//! for, and the settings file in a footer pinned to the bottom under a rule, as the Diagnostics tab
//! pins its compiler.

use er_hotkey_config::{chord_name, parse_chord};
use hudhook::imgui::Ui;

use super::process::{Menu, Tab, spawn_load, spawn_save};
use super::theme::{self, ButtonKind, ButtonSize, Font, SliderRow, footer, gap, tooltip};
use crate::editor::cel::{self, CEL_LAYERS};
use crate::settings::{self, KNOBS, Knob, LAYER_KNOBS, Preset, is_builtin};

/// The room above a section heading, on top of the 10 between blocks, as the Parameters mockup
/// has above `GENERAL`.
const HEADING_SPACE: f32 = 4.0;
/// The knobs' value column at 1080p, wide enough for `0.200` in 12-pixel mono.
const VALUE_WIDTH: f32 = 40.0;
/// The places a knob that is not a whole number is kept to, as its slider's format kept it
/// before the slider lost its text.
const KNOB_DECIMALS: usize = 3;
/// The most of a knob's row its label column takes.
const LABEL_SHARE: f32 = 0.45;
/// The menu key's field at 1080p.
const KEY_FIELD_WIDTH: f32 = 120.0;
/// The narrowest the new preset's name field gets, at 1080p.
const NAME_FIELD_MIN: f32 = 60.0;

/// A knob's value as its row shows it: a whole number bare, a fine range (narrower than 0.5) to
/// three places, any other to two.
fn value_text(knob: &Knob, value: f32) -> String {
    if knob.integer {
        format!("{value:.0}")
    } else if knob.max - knob.min < 0.5 {
        format!("{value:.3}")
    } else {
        format!("{value:.2}")
    }
}

/// A section heading after a block, 14 below it.
fn section(ui: &Ui, words: &str) {
    gap(ui, HEADING_SPACE);
    theme::section_heading(ui, words);
}

impl Menu {
    pub(super) fn draw_look_tab(&mut self, ui: &Ui) {
        let _spacing = theme::spacing(ui, theme::CONTENT_GAP);
        if theme::checkbox(ui, "Cel shading on", &mut self.live.enabled) {
            self.layers.set_cel_enabled(self.live.enabled);
        }
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "Off turns the cel look off: the knobs below and the cel layers. Your other \
                     layers still draw.",
                ],
            );
        }
        self.draw_knobs(ui);
        self.draw_cel_layers(ui);
        self.draw_preset_editor(ui);
        self.draw_menu_key(ui);
        footer(ui, "##settings-file", || self.draw_settings_file(ui));
    }

    /// Each group of knobs under its heading, a slider row each.
    fn draw_knobs(&mut self, ui: &Ui) {
        let t = theme::current();
        let widest = KNOBS
            .iter()
            .map(|knob| ui.calc_text_size(knob.label)[0])
            .fold(0.0_f32, f32::max);
        let most = ui.content_region_avail()[0] * LABEL_SHARE;
        let row = SliderRow {
            label_width: widest.min(most).ceil() / t.scale,
            value_width: VALUE_WIDTH,
            ..SliderRow::PARAMETER
        };
        let mut changed = false;
        let mut group = "";
        for (index, knob) in KNOBS.iter().enumerate() {
            if knob.group != group {
                group = knob.group;
                section(ui, group);
            }
            let value = &mut self.live.look.0[index];
            let shown = value_text(knob, *value);
            let moved = if knob.integer {
                let mut whole = value.round() as i32;
                let moved = row.draw_int(
                    ui,
                    knob.label,
                    &mut whole,
                    [knob.min as i32, knob.max as i32],
                    &shown,
                );
                *value = whole as f32;
                moved
            } else {
                row.draw(
                    ui,
                    knob.label,
                    value,
                    [knob.min, knob.max],
                    KNOB_DECIMALS,
                    &shown,
                )
            };
            if ui.is_item_hovered() {
                tooltip(ui, &[knob.help]);
            }
            if index == 0 {
                theme::register_rect("look_slider", theme::last_track());
            }
            changed |= moved;
        }
        if changed {
            self.live.look = self.live.look.clamped();
        }
    }

    /// The stock cel layers: one line each with its values and whether it draws, and a button
    /// that shows it in the Layers tab. A layer the project does not have says how it comes back.
    fn draw_cel_layers(&mut self, ui: &Ui) {
        let t = theme::current();
        section(ui, "Surfaces and final picture");
        theme::note(
            ui,
            "Layers in the Layers tab, first at their stage. A preset sets them; edit them there.",
        );
        let values = self.layers.cel_values();
        let mut show = None;
        for stock in &CEL_LAYERS {
            let template = stock.template();
            let found =
                cel::find(self.layers.project(), stock).map(|layer| (layer.id, layer.enabled));
            let shown: Vec<String> = stock
                .params
                .iter()
                .map(|&(_, index)| {
                    let knob = &LAYER_KNOBS[index];
                    format!("{} {}", knob.label, value_text(knob, values.0[index]))
                })
                .collect();
            let state = match found {
                Some((_, true)) => "on".to_owned(),
                Some((_, false)) => "off".to_owned(),
                None => "not in the project".to_owned(),
            };
            let size = ButtonSize::SMALL;
            let label = format!("Show##cel-{}", stock.template);
            let width = theme::button_width(ui, &label, ButtonKind::Secondary, size);
            let mut row = theme::Row::begin(ui, size.height, theme::TOOLBAR_PRESET_GAP);
            let words = format!("{}: {state}", template.label);
            let words_width = (row.remaining() - width - t.px(theme::TOOLBAR_PRESET_GAP))
                .max(t.px(NAME_FIELD_MIN));
            row.next(ui, [words_width, theme::line_height(ui, Font::Body)]);
            theme::text(ui, Font::Body, theme::TEXT, &words);
            if ui.is_item_hovered() {
                tooltip(ui, &[template.summary]);
            }
            if let Some((id, _)) = found {
                row.next(ui, [width, t.px(size.height).round()]);
                if theme::button(ui, &label, ButtonKind::Secondary, size) {
                    show = Some(id);
                }
            }
            row.end(ui);
            if found.is_some() {
                theme::mono_quiet(ui, &shown.join(", "));
            } else {
                theme::quiet(
                    ui,
                    "Apply a preset that uses it, or + Layer in the Layers tab.",
                );
            }
        }
        if let Some(id) = show {
            self.layers.select(id);
            self.tab = Tab::Layers;
        }
    }

    /// Presets: a name and Save as preset, which keeps the knobs and the cel layers' values as a
    /// preset of the player's own; and Delete preset while the chosen preset is one.
    fn draw_preset_editor(&mut self, ui: &Ui) {
        let t = theme::current();
        section(ui, "Presets");
        theme::note(
            ui,
            "Keeps the knobs and the cel layers' values under a name of your own, for the \
             toolbar's preset menu.",
        );
        let deletable = !is_builtin(&self.live.preset)
            && self.live.presets.iter().any(|p| p.name == self.live.preset);
        let size = ButtonSize::SMALL;
        let save_label = "Save as preset##look";
        let delete_label = "Delete preset##look";
        let save_width = theme::button_width(ui, save_label, ButtonKind::Primary, size);
        let delete_width = theme::button_width(ui, delete_label, ButtonKind::Secondary, size);
        let gap = t.px(theme::TOOLBAR_PRESET_GAP).round();
        let frame = t.px(theme::FRAME_HEIGHT).round();
        let mut row = theme::Row::begin(ui, theme::FRAME_HEIGHT, theme::TOOLBAR_PRESET_GAP);
        let buttons = save_width + if deletable { gap + delete_width } else { 0.0 };
        let field_width = (row.remaining() - buttons).max(t.px(NAME_FIELD_MIN));
        row.next(ui, [field_width, frame]);
        ui.set_next_item_width(field_width);
        ui.input_text("##new preset", &mut self.new_preset_name)
            .hint("new preset name")
            .build();
        row.next(ui, [save_width, frame]);
        let saved = theme::button(ui, save_label, ButtonKind::Primary, size);
        theme::register_item(ui, "look_preset_save");
        if saved {
            let name = self.new_preset_name.trim().to_owned();
            if name.is_empty() {
                self.status = "Type a name for the preset first.".to_owned();
            } else if is_builtin(&name) {
                self.status = format!("{name} is a built-in preset; pick another name.");
            } else {
                let layers = self.layers.cel_values();
                let preset = Preset {
                    name: name.clone(),
                    look: self.live.look,
                    layers,
                };
                match self.live.presets.iter_mut().find(|p| p.name == name) {
                    Some(existing) => *existing = preset,
                    None => self.live.presets.push(preset),
                }
                self.live.preset.clone_from(&name);
                self.preset_look = Some((self.live.look, layers));
                self.new_preset_name.clear();
                self.saved = self.live.clone();
                self.status = format!("Saving preset {name}...");
                spawn_save(self.live.clone());
            }
        }
        if deletable {
            let name = self.live.preset.clone();
            row.next(ui, [delete_width, frame]);
            if theme::button(ui, delete_label, ButtonKind::Secondary, size) {
                self.live.presets.retain(|p| p.name != name);
                self.live.preset = settings::DEFAULT_PRESET.to_owned();
                self.preset_look = None;
                self.saved = self.live.clone();
                self.status = format!("Deleted {name}; saving...");
                spawn_save(self.live.clone());
            }
            if ui.is_item_hovered() {
                tooltip(
                    ui,
                    &[format!(
                        "Takes {name} out of the presets and goes back to {}.",
                        settings::DEFAULT_PRESET
                    )],
                );
            }
        }
        row.end(ui);
    }

    /// The menu key: a mono field, Enter to apply, and a quiet hint beside it when there is room.
    fn draw_menu_key(&mut self, ui: &Ui) {
        let t = theme::current();
        section(ui, "Menu key");
        let field = t.px(KEY_FIELD_WIDTH).round();
        let frame = t.px(theme::FRAME_HEIGHT).round();
        let hint = "F6, Home or ctrl+F6, then Enter";
        let mut row = theme::Row::begin(ui, theme::FRAME_HEIGHT, theme::TOOLBAR_PRESET_GAP);
        row.next(ui, [field, frame]);
        let entered = {
            let _font = theme::font(ui, Font::Mono);
            let _frame = theme::frame(ui, theme::FRAME_HEIGHT, theme::MONO_FRAME_PAD_X);
            ui.set_next_item_width(field);
            ui.input_text("##menu key", &mut self.key_text)
                .enter_returns_true(true)
                .build()
        };
        if ui.is_item_hovered() {
            tooltip(
                ui,
                &[
                    "The key that opens and closes this panel: a key name such as F6, Home or \
                   ctrl+F6, then Enter. Save keeps it for next time.",
                ],
            );
        }
        let hint_size = [
            theme::text_width(ui, Font::Label, hint),
            theme::line_height(ui, Font::Label),
        ];
        if row.remaining() >= hint_size[0] {
            row.next(ui, hint_size);
            theme::quiet(ui, hint);
        }
        row.end(ui);
        if entered {
            match parse_chord(self.key_text.trim()) {
                Ok(chord) => {
                    self.menu_key = chord;
                    self.live.menu_key = self.key_text.trim().to_owned();
                    self.status = format!(
                        "Menu key is now {}. Save keeps it for next time.",
                        chord_name(chord)
                    );
                }
                Err(error) => self.status = format!("Not a key: {error}"),
            }
        }
    }

    /// The settings file's footer: Reload, which reads it again for values edited there by hand,
    /// and where it is.
    fn draw_settings_file(&mut self, ui: &Ui) {
        let t = theme::current();
        theme::section_heading(ui, "Settings file");
        let size = ButtonSize::SMALL;
        let label = "Reload##look";
        let note = "reads it again, for values edited there by hand";
        let mut row = theme::Row::begin(ui, size.height, theme::TOOLBAR_PRESET_GAP);
        let width = theme::button_width(ui, label, ButtonKind::Secondary, size);
        row.next(ui, [width, t.px(size.height).round()]);
        if theme::button(ui, label, ButtonKind::Secondary, size) {
            self.status = "Reading...".to_owned();
            spawn_load();
        }
        let note_size = [
            theme::text_width(ui, Font::Label, note),
            theme::line_height(ui, Font::Label),
        ];
        if row.remaining() >= note_size[0] {
            row.next(ui, note_size);
            theme::quiet(ui, note);
        }
        row.end(ui);
        theme::mono_quiet(ui, &format!("<Game>\\{}", settings::FILE_NAME));
    }
}
