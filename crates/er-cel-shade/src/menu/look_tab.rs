//! The Look tab (design 9): the cel knobs and presets the F6 menu had before the editor, now one
//! tab of the Shaders panel. The preset combo, Save and Revert sit in the panel's toolbar; what is
//! left here is cel on or off, the knobs, saving the look as a preset, the menu key, and reading
//! `er-cel-shade.toml` again.

use er_hotkey_config::{chord_name, parse_chord};
use hudhook::imgui::Ui;

use super::HEADING_TEXT;
use super::process::{Menu, spawn_load, spawn_save};
use crate::settings::{self, KNOBS, Preset, is_builtin};

impl Menu {
    pub(super) fn draw_look_tab(&mut self, ui: &Ui) {
        ui.checkbox("Cel shading on", &mut self.live.enabled);
        if ui.is_item_hovered() {
            let _tooltip = ui.begin_tooltip();
            ui.text("Off turns the cel look off. Layers in the Layers tab still draw.");
        }

        let mut changed = false;
        let mut group = "";
        for (index, knob) in KNOBS.iter().enumerate() {
            if knob.group != group {
                group = knob.group;
                ui.spacing();
                ui.text_colored(HEADING_TEXT, group);
            }
            let value = &mut self.live.look.0[index];
            let moved = if knob.integer {
                let mut whole = value.round() as i32;
                let moved = ui.slider(knob.label, knob.min as i32, knob.max as i32, &mut whole);
                *value = whole as f32;
                moved
            } else {
                ui.slider_config(knob.label, knob.min, knob.max)
                    .display_format("%.3f")
                    .build(value)
            };
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text(knob.help);
            }
            changed |= moved;
        }
        if changed {
            self.live.look = self.live.look.clamped();
        }

        ui.spacing();
        ui.separator();
        self.draw_preset_editor(ui);
        self.draw_menu_key(ui);
        ui.spacing();
        if ui.button("Reload file") {
            self.status = "Reading...".to_owned();
            spawn_load();
        }
        if ui.is_item_hovered() {
            let _tooltip = ui.begin_tooltip();
            ui.text(format!(
                "Reads {} again, for values edited there by hand.",
                settings::FILE_NAME
            ));
        }
    }

    fn draw_preset_editor(&mut self, ui: &Ui) {
        ui.spacing();
        ui.set_next_item_width(ui.content_region_avail()[0] * 0.55);
        ui.input_text("##new preset", &mut self.new_preset_name)
            .hint("new preset name")
            .build();
        ui.same_line();
        if ui.button("Save as preset") {
            let name = self.new_preset_name.trim().to_owned();
            if name.is_empty() {
                self.status = "Type a name for the preset first.".to_owned();
            } else if is_builtin(&name) {
                self.status = format!("{name} is a built-in preset; pick another name.");
            } else {
                let preset = Preset {
                    name: name.clone(),
                    look: self.live.look,
                };
                match self.live.presets.iter_mut().find(|p| p.name == name) {
                    Some(existing) => *existing = preset,
                    None => self.live.presets.push(preset),
                }
                self.live.preset.clone_from(&name);
                self.preset_look = Some(self.live.look);
                self.new_preset_name.clear();
                self.saved = self.live.clone();
                self.status = format!("Saving preset {name}...");
                spawn_save(self.live.clone());
            }
        }
        if !is_builtin(&self.live.preset)
            && self.live.presets.iter().any(|p| p.name == self.live.preset)
        {
            ui.same_line();
            if ui.button("Delete preset") {
                let name = self.live.preset.clone();
                self.live.presets.retain(|p| p.name != name);
                self.live.preset = settings::DEFAULT_PRESET.to_owned();
                self.preset_look = None;
                self.saved = self.live.clone();
                self.status = format!("Deleted {name}; saving...");
                spawn_save(self.live.clone());
            }
        }
    }

    fn draw_menu_key(&mut self, ui: &Ui) {
        ui.spacing();
        ui.set_next_item_width(ui.content_region_avail()[0] * 0.35);
        let entered = ui
            .input_text("Menu key", &mut self.key_text)
            .enter_returns_true(true)
            .build();
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
        if ui.is_item_hovered() {
            let _tooltip = ui.begin_tooltip();
            ui.text("A key name such as F6, Home or ctrl+F6, then Enter.");
        }
    }
}
