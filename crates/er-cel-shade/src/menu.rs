//! The in-game settings menu: a hotkey opens it, it takes the game's input while open, and every
//! slider changes the picture on the next frame.
//!
//! # Threads
//!
//! Everything here runs on the render thread, inside the overlay host's `Present`, except file
//! input and output: Save and Reload hand the work to a short-lived thread and pick up its result
//! on a later frame, so a slow disk never stalls a frame.
//!
//! # What the menu changes
//!
//! A knob writes [`crate::look_binding::publish`], which updates the mapped buffer every replaced
//! shader reads. Nothing is written to disk until Save; Reload reads the file back, so values edited
//! by hand in `er-cel-shade.toml` can be loaded without restarting the game.

use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use er_hotkey_config::keys::{MODIFIER_ALT, MODIFIER_CTRL, MODIFIER_SHIFT};
use er_hotkey_config::{Chord, chord_name, parse_chord};
use hudhook::imgui::{Condition, StyleColor, TreeNodeFlags, Ui};
use windows::Win32::System::Threading::GetCurrentProcessId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, VK_CONTROL, VK_ESCAPE, VK_MENU, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use crate::settings::{
    self, DEFAULT_MENU_KEY, KNOBS, Look, Preset, Settings, builtin_presets, is_builtin,
};
use crate::{hooks, input, log, look_binding};

/// The menu is laid out for a 1080-line display and scaled from there.
const REFERENCE_HEIGHT: f32 = 1080.0;
/// Text size relative to imgui's default face at 1080p, which is small from a sofa.
const BASE_FONT_SCALE: f32 = 1.3;
/// Window width at 1080p.
const WINDOW_WIDTH: f32 = 560.0;

struct Menu {
    /// What the game is showing.
    live: Settings,
    /// What the file holds, as of the last save or load; the difference is "unsaved".
    saved: Settings,
    /// The look the chosen preset had when it was applied, so an edit can be shown as one.
    preset_look: Option<Look>,
    menu_key: Chord,
    key_text: String,
    new_preset_name: String,
    status: String,
}

static MENU: Mutex<Option<Menu>> = Mutex::new(None);
static OPEN: AtomicBool = AtomicBool::new(false);
/// A finished Save or Reload, for the next frame to show or apply.
static FILE_RESULT: Mutex<Option<FileResult>> = Mutex::new(None);

enum FileResult {
    Saved(Result<PathBuf, String>),
    Loaded(Box<Settings>, Vec<String>),
}

/// The settings file's path, beside the game executable.
pub fn settings_path() -> PathBuf {
    er_game_base::log::game_directory_path()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(settings::FILE_NAME)
}

/// Read the settings file, or the defaults when there is none.
pub fn load_settings() -> (Settings, Vec<String>) {
    match std::fs::read_to_string(settings_path()) {
        Ok(text) => settings::parse(&text),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            (Settings::default(), Vec::new())
        }
        Err(error) => (
            Settings::default(),
            vec![format!("could not read {}: {error}", settings::FILE_NAME)],
        ),
    }
}

fn parse_menu_key(text: &str) -> Chord {
    parse_chord(text).unwrap_or_else(|error| {
        log(format_args!(
            "menu_key `{text}` not understood ({error}); using {DEFAULT_MENU_KEY}"
        ));
        parse_chord(DEFAULT_MENU_KEY).unwrap_or(Chord {
            modifiers: 0,
            vk: 0x75,
            dik: None,
        })
    })
}

/// Start with these settings: publish their look and remember them as what the file holds.
pub fn init(settings: Settings, warnings: &[String]) {
    for warning in warnings {
        log(format_args!("{}: {warning}", settings::FILE_NAME));
    }
    look_binding::publish(settings.look.gpu_block(settings.enabled));
    let menu_key = parse_menu_key(&settings.menu_key);
    log(format_args!(
        "settings: preset `{}` enabled={} menu key {}",
        settings.preset,
        settings.enabled,
        chord_name(menu_key)
    ));
    let preset_look = all_presets(&settings)
        .into_iter()
        .find(|p| p.name == settings.preset)
        .map(|p| p.look);
    if let Ok(mut slot) = MENU.lock() {
        *slot = Some(Menu {
            key_text: settings.menu_key.clone(),
            live: settings.clone(),
            saved: settings,
            preset_look,
            menu_key,
            new_preset_name: String::new(),
            status: String::new(),
        });
    }
}

/// Is the menu on screen?
pub fn is_open() -> bool {
    OPEN.load(Ordering::Acquire)
}

fn set_open(open: bool) {
    if OPEN.swap(open, Ordering::AcqRel) != open {
        input::set_taking(open);
        log(format_args!(
            "menu {}",
            if open { "opened" } else { "closed" }
        ));
    }
}

fn all_presets(settings: &Settings) -> Vec<Preset> {
    let mut presets = builtin_presets();
    presets.extend(settings.presets.iter().cloned());
    presets
}

fn key_down(vk: u32) -> bool {
    // SAFETY: a plain query of the asynchronous key state.
    (unsafe { GetAsyncKeyState(vk as i32) } as u16 & 0x8000) != 0
}

fn chord_held(chord: Chord) -> bool {
    let modifier = |bit: u8, vk: u16| chord.modifiers & bit == 0 || key_down(u32::from(vk));
    modifier(MODIFIER_CTRL, VK_CONTROL.0)
        && modifier(MODIFIER_ALT, VK_MENU.0)
        && modifier(MODIFIER_SHIFT, VK_SHIFT.0)
        && key_down(chord.vk)
}

/// Does the foreground window belong to the game? `GetAsyncKeyState` sees keys pressed in every
/// application, and a key typed into a browser must not open the menu.
fn game_has_focus() -> bool {
    // SAFETY: plain window queries; a null window yields process id 0.
    unsafe {
        let window = GetForegroundWindow();
        let mut process = 0;
        GetWindowThreadProcessId(window, Some(&mut process));
        process == GetCurrentProcessId()
    }
}

/// The menu key and Escape, edge-detected once per frame.
fn poll_keys(menu_key: Chord) {
    static KEY_WAS_DOWN: AtomicBool = AtomicBool::new(false);
    static ESCAPE_WAS_DOWN: AtomicBool = AtomicBool::new(false);
    let focused = game_has_focus();
    let key = focused && chord_held(menu_key);
    let escape = focused && key_down(u32::from(VK_ESCAPE.0));
    let key_pressed = key && !KEY_WAS_DOWN.swap(key, Ordering::Relaxed);
    if !key {
        KEY_WAS_DOWN.store(false, Ordering::Relaxed);
    }
    let escape_pressed = escape && !ESCAPE_WAS_DOWN.swap(escape, Ordering::Relaxed);
    if !escape {
        ESCAPE_WAS_DOWN.store(false, Ordering::Relaxed);
    }
    if key_pressed {
        set_open(!is_open());
    } else if escape_pressed && is_open() {
        set_open(false);
    }
}

fn spawn_save(settings: Settings) {
    let _ = std::thread::Builder::new()
        .name("er-cel-shade-save".to_owned())
        .spawn(move || {
            let path = settings_path();
            let result = er_hotkey_config::write_atomic(&path, &settings::render(&settings))
                .map(|()| path)
                .map_err(|error| error.to_string());
            if let Ok(mut slot) = FILE_RESULT.lock() {
                *slot = Some(FileResult::Saved(result));
            }
        });
}

fn spawn_load() {
    let _ = std::thread::Builder::new()
        .name("er-cel-shade-load".to_owned())
        .spawn(|| {
            let (settings, warnings) = load_settings();
            if let Ok(mut slot) = FILE_RESULT.lock() {
                *slot = Some(FileResult::Loaded(Box::new(settings), warnings));
            }
        });
}

impl Menu {
    fn publish(&self) {
        look_binding::publish(self.live.look.gpu_block(self.live.enabled));
    }

    fn apply_preset(&mut self, preset: &Preset) {
        self.live.look = preset.look;
        self.live.preset.clone_from(&preset.name);
        self.preset_look = Some(preset.look);
        self.publish();
        self.status = format!("Applied {}. Save keeps it for next time.", preset.name);
    }

    fn take_file_result(&mut self) {
        let Some(result) = FILE_RESULT.lock().ok().and_then(|mut slot| slot.take()) else {
            return;
        };
        match result {
            FileResult::Saved(Ok(path)) => {
                self.status = format!("Saved to {}", path.display());
                log(format_args!("settings saved to {}", path.display()));
            }
            FileResult::Saved(Err(error)) => {
                self.status = format!("Save failed: {error}");
                log(format_args!("settings save failed: {error}"));
            }
            FileResult::Loaded(settings, warnings) => {
                self.status = if warnings.is_empty() {
                    format!("Reloaded {}", settings::FILE_NAME)
                } else {
                    format!(
                        "Reloaded with {} problem(s): {}",
                        warnings.len(),
                        warnings[0]
                    )
                };
                for warning in &warnings {
                    log(format_args!("{}: {warning}", settings::FILE_NAME));
                }
                self.menu_key = parse_menu_key(&settings.menu_key);
                self.key_text.clone_from(&settings.menu_key);
                self.preset_look = all_presets(&settings)
                    .into_iter()
                    .find(|p| p.name == settings.preset)
                    .map(|p| p.look);
                self.live = (*settings).clone();
                self.saved = *settings;
                self.publish();
            }
        }
    }

    fn edited(&self) -> bool {
        self.preset_look.is_some_and(|look| look != self.live.look)
    }

    fn draw(&mut self, ui: &Ui) {
        let display = ui.io().display_size;
        let scale = (display[1] / REFERENCE_HEIGHT).max(0.75);
        let mut open = true;
        ui.window("Cel shading")
            .opened(&mut open)
            .position(
                [display[0] * 0.5, display[1] * 0.5],
                Condition::FirstUseEver,
            )
            .position_pivot([0.5, 0.5])
            .size([WINDOW_WIDTH * scale, 0.0], Condition::FirstUseEver)
            .build(|| {
                ui.set_window_font_scale(BASE_FONT_SCALE * scale);
                self.draw_contents(ui);
            });
        if !open {
            set_open(false);
        }
    }

    fn draw_contents(&mut self, ui: &Ui) {
        ui.text_disabled(format!(
            "{} or Esc closes this. The game gets no input while it is open.",
            chord_name(self.menu_key)
        ));
        ui.separator();

        if ui.checkbox("Cel shading on", &mut self.live.enabled) {
            self.publish();
        }

        let presets = all_presets(&self.live);
        let shown = if self.edited() {
            format!("{} (edited)", self.live.preset)
        } else {
            self.live.preset.clone()
        };
        let mut picked = None;
        if let Some(combo) = ui.begin_combo("Preset", &shown) {
            for (at, preset) in presets.iter().enumerate() {
                if at == builtin_presets().len() {
                    ui.separator();
                }
                if ui
                    .selectable_config(&preset.name)
                    .selected(preset.name == self.live.preset)
                    .build()
                {
                    picked = Some(preset.clone());
                }
            }
            combo.end();
        }
        if let Some(preset) = picked {
            self.apply_preset(&preset);
        }

        let mut changed = false;
        let mut group = "";
        for (index, knob) in KNOBS.iter().enumerate() {
            if knob.group != group {
                group = knob.group;
                ui.spacing();
                ui.text_colored([0.95, 0.8, 0.45, 1.0], group);
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
                ui.tooltip_text(knob.help);
            }
            changed |= moved;
        }
        if changed {
            self.live.look = self.live.look.clamped();
            self.publish();
        }

        ui.spacing();
        ui.separator();
        self.draw_file_buttons(ui);
        self.draw_preset_editor(ui);
        self.draw_menu_key(ui);

        if !self.status.is_empty() {
            ui.spacing();
            ui.text_wrapped(&self.status);
        }
        if ui.collapsing_header("Diagnostics", TreeNodeFlags::empty()) {
            draw_diagnostics(ui);
        }
    }

    fn draw_file_buttons(&mut self, ui: &Ui) {
        let unsaved = self.live != self.saved;
        if ui.button("Save") {
            self.saved = self.live.clone();
            self.status = "Saving...".to_owned();
            spawn_save(self.live.clone());
        }
        ui.same_line();
        if ui.button("Reload file") {
            self.status = "Reading...".to_owned();
            spawn_load();
        }
        ui.same_line();
        if ui.button("Revert") {
            self.live = self.saved.clone();
            self.preset_look = all_presets(&self.live)
                .into_iter()
                .find(|p| p.name == self.live.preset)
                .map(|p| p.look);
            self.publish();
            self.status = "Back to the saved settings.".to_owned();
        }
        if unsaved {
            ui.same_line();
            let _colour = ui.push_style_color(StyleColor::Text, [1.0, 0.6, 0.4, 1.0]);
            ui.text("unsaved changes");
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
            ui.tooltip_text("A key name such as F6, Home or ctrl+F6, then Enter.");
        }
    }
}

fn draw_diagnostics(ui: &Ui) {
    let relaxed = |a: &std::sync::atomic::AtomicUsize| a.load(Ordering::Relaxed);
    ui.text(format!(
        "Root signatures carrying the look: {}",
        look_binding::augmented_root_signatures()
    ));
    ui.text(format!(
        "Left as the game made them: {} full, {} other",
        relaxed(&look_binding::SKIPPED_NO_ROOM),
        relaxed(&look_binding::SKIPPED_OTHER)
    ));
    ui.text(format!(
        "Passes replaced: {} (left vanilla: {})",
        relaxed(&hooks::SWAPS),
        relaxed(&hooks::SWAPS_REFUSED)
    ));
    ui.text(format!(
        "Pipelines the driver refused, created vanilla instead: {}",
        relaxed(&hooks::REWRITES_REFUSED)
    ));
    ui.text(format!(
        "Look bound to command lists: {}",
        relaxed(&look_binding::BINDS)
    ));
    ui.text(format!(
        "Pipelines given a root signature: {} (none found: {})",
        relaxed(&hooks::NO_ROOT_SIGNATURE_FIXED),
        relaxed(&hooks::NO_ROOT_SIGNATURE_LEFT)
    ));
    ui.text(format!(
        "Cursor freed: {} (hooked: {})",
        relaxed(&input::CURSOR_FORCED),
        input::CURSOR_HOOKED.load(Ordering::Relaxed)
    ));
    ui.text(format!(
        "Game reads blanked: mouse {}, keys {}, pad {}",
        er_dinput_suppress_core::suppressed_mouse_reads(),
        er_dinput_suppress_core::suppressed_keys(),
        relaxed(&input::PAD_SUPPRESSED)
    ));
}

/// The menu's per-frame entry, called by the overlay whether or not the menu is open: the key that
/// opens it is polled here.
pub fn draw(ui: &Ui) {
    static ARMED: AtomicBool = AtomicBool::new(false);
    if !ARMED.load(Ordering::Relaxed) {
        // Retried each frame until DirectInput and XInput are loaded; cheap once both are armed.
        let dinput = input::try_install_dinput();
        let pad = input::try_install_pad();
        ARMED.store(dinput && pad, Ordering::Relaxed);
    }
    let Ok(mut slot) = MENU.lock() else {
        return;
    };
    let Some(menu) = slot.as_mut() else {
        return;
    };
    menu.take_file_result();
    poll_keys(menu.menu_key);
    if !is_open() {
        return;
    }
    menu.draw(ui);
}
