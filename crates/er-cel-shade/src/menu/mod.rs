//! The Shaders panel (design 9): a hotkey opens it, it takes the game's input while open, and it is
//! pinned to one edge of the screen, so the frame it edits stays in view.
//!
//! # Layout
//!
//! Docked right by default, full height, [`crate::settings::DEFAULT_PANEL_WIDTH`] pixels wide at
//! 1080p times the display's scale, so about three quarters of the frame stays clear. Dock is
//! Right, Left or Floating and is saved with the menu key. A docked panel's position and size are
//! set every frame from the display size ([`docked_rect`]), which needs no imgui docking. Its
//! inner edge drags to widen it ([`edge_rect`], [`width_from_edge`]); the Code sub-tab keeps a
//! width of its own, 720 pixels by default, which fits 100 columns. The header's arrow collapses
//! it to a [`STRIP_WIDTH`] strip, the state P4's picking from the world will use.
//!
//! Inside it is one column: the header, the toolbar (Vanilla, the preset, Save and Revert, and a
//! status line naming the compiler, the probe and the hooks' cost), then the tabs: Layers
//! ([`layers_tab`]), Look (`look_tab`, today's cel knobs and presets) and Diagnostics
//! ([`diagnostics_tab`]). P2's Hunt tab goes between Look and Diagnostics.
//!
//! Holding the backtick key shows vanilla while it is held ([`Momentary`]), and holding Tab hides
//! the panel while it is held; neither does anything while a text field has the keyboard, where
//! both keys are typed.
//!
//! # Threads
//!
//! Everything here runs on the render thread, inside the overlay host's `Present`, except file
//! input and output: Save and Reload of the settings hand the work to a short-lived thread, and
//! the layers' files go through [`files`]' thread. Either answer is picked up on a later frame, so
//! a slow disk never stalls a frame.
//!
//! # What the panel changes once a present
//!
//! Once per present, whether or not the panel is open, [`end_frame`] stages what the mode, the
//! look and the unfaithful ports ask for ([`crate::safety::staged`]): the cel registers of the
//! parameter block, which stages have content and which port kinds are off, which together decide
//! the ports the command-list hooks redirect. A layer change the worker has not read back yet is
//! staged first ([`layers_tab`]). It then publishes the parameter block and reports the present to
//! the graveyard, so a slider moved this frame is on screen the same frame. The settings file is
//! written only by Save; Reload reads it back, so values edited by hand in `er-cel-shade.toml` can
//! be loaded without restarting the game.
//!
//! [`init`] stages the saved settings once before the game's device exists, so cel is on from the
//! first frame even when the overlay never draws: the parameter ring is filled from what is staged
//! when it is created, and the stages hold until something restages them. Without the overlay the
//! present count stays 0, so the probe has no clock and the look cannot be changed live.
//!
//! One caveat on that: the list slots in [`crate::cmdlist`] go stale by the present count, so
//! without it a slot left by a list closed on another thread is reclaimed only once it owes the
//! probe nothing.
//!
//! # A panic in the panel
//!
//! The overlay's guest draw runs [`draw_ui`] inside `catch_unwind` (`crate::overlay`). imgui-rs's
//! tokens end every window, child, combo and tooltip as the panic unwinds, so Dear ImGui's stacks
//! stay balanced; the panel is then hidden for the rest of the session ([`hide_for_session`]), the
//! game gets its input back, and [`end_frame`] -- outside that `catch_unwind` -- keeps staging and
//! publishing, so the look and the layers go on drawing. A panic in the Layers tab's own tick,
//! which [`end_frame`] runs, hides the panel the same way, and the publish after it still runs.
//!
//! # Glyphs
//!
//! The host's font has only `GLYPH_RANGES` (`0x20-0x17F`, `0x2000-0x206F`); anything else draws as
//! a box. Every string the panel's code writes is ASCII or inside those ranges, the status lights
//! are circles on the draw list, and a test reads every string literal under `src/menu/` and fails
//! on one that is not.
//!
//! # Telemetry
//!
//! The panel counts what the player does in it ([`telemetry_counters`]): the code editor's edit
//! callbacks and pastes, the files it wrote, parameter and stack edits, caught panics and its own
//! time per frame. The worker writes them into the telemetry file with the rest.

pub mod code;
pub mod diagnostics_tab;
pub mod files;
pub mod layers_tab;
#[cfg(windows)]
mod look_tab;
pub mod widgets;

use std::sync::atomic::{AtomicU64, Ordering};

use crate::settings::{Dock, clamp_panel_width};

/// The collapsed panel's width at 1080p.
pub const STRIP_WIDTH: f32 = 40.0;
/// The width of the grab strip along the docked panel's inner edge, at 1080p.
pub const EDGE_GRAB: f32 = 6.0;
/// The most of the screen's width the panel takes, whatever its saved width.
pub const MAX_SCREEN_SHARE: f32 = 0.8;
/// The panel's background opacity: the frame shows faintly through it.
pub const BACKGROUND_ALPHA: f32 = 0.94;
/// The smallest display scale the layout follows; below it the panel stops shrinking.
pub const MIN_SCALE: f32 = 0.75;
/// A probe region whose header was written within this many presents counts as live.
pub const PROBE_LIVE_PRESENTS: u32 = diagnostics_tab::PROBE_LIVE_PRESENTS;
/// Presents between two reads of the probe for the toolbar's chip.
pub const PROBE_CHIP_PERIOD: u64 = 15;

/// Colours, as Dear ImGui's RGBA floats.
pub const HEADING_TEXT: [f32; 4] = [0.95, 0.8, 0.45, 1.0];
pub const ERROR_TEXT: [f32; 4] = [1.0, 0.45, 0.35, 1.0];
pub const WARNING_TEXT: [f32; 4] = [1.0, 0.75, 0.35, 1.0];
pub const QUIET_TEXT: [f32; 4] = [0.65, 0.64, 0.6, 1.0];
/// A pressed toggle button, such as a layer's `S` while it is soloed.
pub const BUTTON_ON: [f32; 4] = [0.6, 0.45, 0.18, 1.0];
/// The status lights: live, compiling, error, off.
pub const LIGHT_LIVE: [f32; 4] = [0.36, 0.76, 0.48, 1.0];
pub const LIGHT_BUSY: [f32; 4] = [0.85, 0.66, 0.31, 1.0];
pub const LIGHT_ERROR: [f32; 4] = [0.9, 0.35, 0.3, 1.0];
pub const LIGHT_OFF: [f32; 4] = [0.37, 0.37, 0.37, 1.0];

/// `EDIT` callbacks of the code editor: one per change to its text.
pub static EDIT_CALLBACKS: AtomicU64 = AtomicU64::new(0);
/// Ctrl+V or Shift+Insert while the code editor had the keyboard.
pub static PASTES: AtomicU64 = AtomicU64::new(0);
/// Layer files and project files the panel wrote.
pub static CODE_SAVES: AtomicU64 = AtomicU64::new(0);
pub static PROJECT_SAVES: AtomicU64 = AtomicU64::new(0);
/// Frames a parameter widget changed a value or reset one.
pub static PARAM_EDITS: AtomicU64 = AtomicU64::new(0);
/// Changes the panel made to the project: parameters, headers, the stack.
pub static STACK_EDITS: AtomicU64 = AtomicU64::new(0);
/// Panics caught in the panel's drawing; after the first it stays hidden.
pub static UI_PANICS: AtomicU64 = AtomicU64::new(0);
/// Microseconds the panel took on the last frame, and the most it took on one.
pub static UI_US_LAST: AtomicU64 = AtomicU64::new(0);
pub static UI_US_MAX: AtomicU64 = AtomicU64::new(0);

/// The panel's counters, by the names the telemetry file gives them.
#[must_use]
pub fn telemetry_counters() -> [(&'static str, u64); 9] {
    let read = |counter: &AtomicU64| counter.load(Ordering::Relaxed);
    [
        ("editor_edit_callbacks", read(&EDIT_CALLBACKS)),
        ("editor_pastes", read(&PASTES)),
        ("editor_code_saves", read(&CODE_SAVES)),
        ("editor_project_saves", read(&PROJECT_SAVES)),
        ("editor_param_edits", read(&PARAM_EDITS)),
        ("editor_stack_edits", read(&STACK_EDITS)),
        ("editor_ui_panics", read(&UI_PANICS)),
        ("editor_ui_us_last", read(&UI_US_LAST)),
        ("editor_ui_us_max", read(&UI_US_MAX)),
    ]
}

/// The factor the layout is scaled by: the display's height over 1080, from the overlay frame,
/// no smaller than [`MIN_SCALE`]. A scale that is not a number is 1.
#[must_use]
pub fn panel_scale(ui_scale: f32) -> f32 {
    if ui_scale.is_finite() && ui_scale > 0.0 {
        ui_scale.max(MIN_SCALE)
    } else {
        1.0
    }
}

/// A rectangle on the screen, in pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PanelRect {
    pub pos: [f32; 2],
    pub size: [f32; 2],
}

/// The docked panel on a `display`, `width` pixels wide at 1080p before `scale` and never more
/// than [`MAX_SCREEN_SHARE`] of the screen, full height against its edge. `None` for a floating
/// panel, which the player places.
#[must_use]
pub fn docked_rect(display: [f32; 2], dock: Dock, width: f32, scale: f32) -> Option<PanelRect> {
    let pixels = (width * scale).min(display[0] * MAX_SCREEN_SHARE).max(1.0);
    let x = match dock {
        Dock::Right => display[0] - pixels,
        Dock::Left => 0.0,
        Dock::Floating => return None,
    };
    Some(PanelRect {
        pos: [x, 0.0],
        size: [pixels, display[1]],
    })
}

/// The grab strip just outside a docked `panel`'s inner edge: the side facing the frame.
#[must_use]
pub fn edge_rect(panel: PanelRect, dock: Dock, scale: f32) -> Option<PanelRect> {
    let grab = EDGE_GRAB * scale;
    let x = match dock {
        Dock::Right => panel.pos[0] - grab,
        Dock::Left => panel.pos[0] + panel.size[0],
        Dock::Floating => return None,
    };
    Some(PanelRect {
        pos: [x, panel.pos[1]],
        size: [grab, panel.size[1]],
    })
}

/// The width at 1080p a docked panel's inner edge dragged to `mouse_x` asks for, kept inside the
/// saved range and [`MAX_SCREEN_SHARE`] of the screen.
#[must_use]
pub fn width_from_edge(display: [f32; 2], dock: Dock, mouse_x: f32, scale: f32) -> Option<f32> {
    let pixels = match dock {
        Dock::Right => display[0] - mouse_x,
        Dock::Left => mouse_x,
        Dock::Floating => return None,
    };
    let widest = display[0] * MAX_SCREEN_SHARE / scale;
    Some(clamp_panel_width((pixels / scale).min(widest)))
}

/// The backtick key as the compare switch: vanilla while it is held. The key has a switch of its
/// own (`safety::Event::Compare`), so releasing it clears only what holding it set, never a
/// vanilla the checkbox, the panic chord or the mode export asked for meanwhile.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Momentary {
    /// The key was held at the last update.
    holding: bool,
}

impl Momentary {
    /// This frame's key state; returns what the compare switch must become when it changes.
    pub fn update(&mut self, held: bool) -> Option<bool> {
        if held == self.holding {
            return None;
        }
        self.holding = held;
        Some(held)
    }
}

#[cfg(windows)]
pub use process::{
    chord_held, draw_ui, end_frame, game_has_focus, hide_for_session, init, load_settings, restage,
    set_unfaithful,
};

#[cfg(windows)]
use process::status_dot;

/// The panel's state and drawing.
#[cfg(windows)]
mod process {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{LazyLock, Mutex, PoisonError};
    use std::time::Instant;

    use er_hotkey_config::keys::{MODIFIER_ALT, MODIFIER_CTRL, MODIFIER_SHIFT};
    use er_hotkey_config::{Chord, chord_name, parse_chord};
    use hudhook::imgui::{Condition, MouseCursor, StyleColor, StyleVar, Ui, WindowFlags};
    use windows::Win32::System::Threading::GetCurrentProcessId;
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        GetAsyncKeyState, VK_CONTROL, VK_ESCAPE, VK_MENU, VK_OEM_3, VK_SHIFT, VK_TAB,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

    use super::diagnostics_tab::{self, compiler_chip, hook_ms_per_frame};
    use super::layers_tab::{Editor, Light};
    use super::{
        BACKGROUND_ALPHA, ERROR_TEXT, LIGHT_BUSY, LIGHT_ERROR, LIGHT_LIVE, LIGHT_OFF, Momentary,
        PROBE_CHIP_PERIOD, PROBE_LIVE_PRESENTS, STRIP_WIDTH, UI_PANICS, UI_US_LAST, UI_US_MAX,
        WARNING_TEXT, docked_rect, edge_rect, panel_scale, width_from_edge,
    };
    use crate::cmdlist::{self, SAMPLE_EVERY, Timing};
    use crate::editor::project as editor_project;
    use crate::editor::worker::{self, Status};
    use crate::probe;
    use crate::safety::{self, Mode, PANIC_CHORD};
    use crate::settings::{
        self, DEFAULT_MENU_KEY, Dock, Look, Preset, Settings, builtin_presets, render_ports,
    };
    use crate::{exports, graveyard, input, log, params, table};

    /// The tabs of the panel.
    #[derive(Clone, Copy, PartialEq, Eq)]
    pub(super) enum Tab {
        Layers,
        Look,
        Diagnostics,
    }

    pub(super) struct Menu {
        /// What the game is showing.
        pub(super) live: Settings,
        /// What the file holds, as of the last save or load; the difference is "unsaved".
        pub(super) saved: Settings,
        /// The look the chosen preset had when it was applied, so an edit can be shown as one.
        pub(super) preset_look: Option<Look>,
        pub(super) menu_key: Chord,
        pub(super) key_text: String,
        pub(super) new_preset_name: String,
        pub(super) status: String,
        tab: Tab,
        collapsed: bool,
        layers: Editor,
        momentary: Momentary,
        /// The toolbar's probe chip, read every [`PROBE_CHIP_PERIOD`] presents.
        probe_live: bool,
        probe_read_at: u64,
    }

    static MENU: Mutex<Option<Menu>> = Mutex::new(None);
    static OPEN: AtomicBool = AtomicBool::new(false);
    /// Set by a panic in the panel: it stays hidden for the rest of the session.
    static HIDDEN: AtomicBool = AtomicBool::new(false);
    /// A finished Save or Reload, for the next frame to show or apply.
    static FILE_RESULT: Mutex<Option<FileResult>> = Mutex::new(None);
    /// `<Game>/er-shader-editor`, where the layers live.
    static EDITOR_DIR: LazyLock<Option<PathBuf>> = LazyLock::new(|| {
        er_game_base::log::game_directory_path().map(|game| game.join(editor_project::DIRECTORY))
    });

    enum FileResult {
        Saved(Result<PathBuf, String>),
        Loaded(Box<Settings>, Vec<String>),
    }

    fn editor_dir() -> Option<&'static Path> {
        EDITOR_DIR.as_deref()
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

    pub(super) fn parse_menu_key(text: &str) -> Chord {
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

    /// Stage what `settings` ask for under the current mode: the cel registers of the next
    /// parameter block, the stages with content -- the editor's layers' among them -- and the port
    /// kinds that are off.
    fn stage(settings: &Settings) {
        let staged = safety::staged(
            safety::mode_setting(),
            &settings.look,
            settings.enabled,
            settings.unfaithful_ports,
            safety::LAYER_STAGES.load(Ordering::Acquire),
        );
        params::set_look(staged.look);
        cmdlist::STAGE_ACTIVE.store(staged.stages, Ordering::Relaxed);
        cmdlist::PORTS_OFF.store(staged.ports_off, Ordering::Relaxed);
        safety::UNFAITHFUL.store(u32::from(settings.unfaithful_ports), Ordering::Relaxed);
    }

    /// Start with these settings: stage them and remember them as what the file holds.
    pub fn init(settings: Settings, warnings: &[String]) {
        for warning in warnings {
            log(format_args!("{}: {warning}", settings::FILE_NAME));
        }
        // Here and not only in `end_frame`, so a session whose overlay never draws still has cel.
        stage(&settings);
        let menu_key = parse_menu_key(&settings.menu_key);
        log(format_args!(
            "settings: preset `{}` enabled={} menu key {} unfaithful_ports=`{}` panel {} {}/{}",
            settings.preset,
            settings.enabled,
            chord_name(menu_key),
            render_ports(settings.unfaithful_ports),
            settings.dock.key(),
            settings.panel_width,
            settings.code_width
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
                tab: Tab::Layers,
                collapsed: false,
                layers: Editor::new(),
                momentary: Momentary::default(),
                probe_live: false,
                probe_read_at: 0,
            });
        }
    }

    /// Stage the live settings again now, for a change made outside the panel's own frame: the
    /// mode export and the editor's worker, which may run while the overlay draws nothing.
    pub fn restage() {
        let slot = MENU.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(menu) = slot.as_ref() {
            stage(&menu.live);
        }
    }

    /// Mark the ports in `bits` unfaithful and no others, keep the mark in the settings file, and
    /// stage it at once. Returns the mark before. For the fidelity test, through the
    /// `er_shader_editor_set_unfaithful_v1` export: only the mark is written to the file, beside
    /// what was last saved, so a look the player has not saved stays unsaved.
    pub fn set_unfaithful(bits: u8) -> u8 {
        let mut slot = MENU.lock().unwrap_or_else(PoisonError::into_inner);
        let Some(menu) = slot.as_mut() else {
            return 0;
        };
        let before = menu.live.unfaithful_ports;
        menu.live.unfaithful_ports = bits;
        menu.saved.unfaithful_ports = bits;
        stage(&menu.live);
        if before != bits {
            menu.status = format!(
                "The fidelity test marked these ports unfaithful: `{}`.",
                render_ports(bits)
            );
            spawn_save(menu.saved.clone());
        }
        before
    }

    /// Is the panel on screen?
    pub(super) fn is_open() -> bool {
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

    /// After a panic in the panel's drawing: hide it for the rest of the session and give the
    /// game its input back. The look and the layers go on drawing.
    pub fn hide_for_session() {
        hide_after_panic("drawing the Shaders panel");
        let mut slot = MENU.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(menu) = slot.as_mut() {
            menu.momentary = Momentary::default();
        }
    }

    /// [`hide_for_session`]'s work that needs no lock on the menu, for a panic caught while the
    /// lock is held: the panel hidden and closed, and the compare key's vanilla, which a backtick
    /// held when it happened would leave on with no panel left to clear it, ended. Only the key's
    /// own request is cleared; a checkbox, panic chord or mode export stands.
    fn hide_after_panic(doing: &str) {
        UI_PANICS.fetch_add(1, Ordering::Relaxed);
        if !HIDDEN.swap(true, Ordering::AcqRel) {
            log(format_args!(
                "panel: a panic while {doing}; it stays hidden until the game restarts, and the \
                 look and the layers go on drawing"
            ));
        }
        set_open(false);
        safety::set_compare(false);
    }

    pub(super) fn all_presets(settings: &Settings) -> Vec<Preset> {
        let mut presets = builtin_presets();
        presets.extend(settings.presets.iter().cloned());
        presets
    }

    fn key_down(vk: u32) -> bool {
        // SAFETY: a plain query of the asynchronous key state.
        (unsafe { GetAsyncKeyState(vk as i32) } as u16 & 0x8000) != 0
    }

    /// Is every key of `chord` down? Extra modifiers do not stop it.
    pub fn chord_held(chord: Chord) -> bool {
        let modifier = |bit: u8, vk: u16| chord.modifiers & bit == 0 || key_down(u32::from(vk));
        modifier(MODIFIER_CTRL, VK_CONTROL.0)
            && modifier(MODIFIER_ALT, VK_MENU.0)
            && modifier(MODIFIER_SHIFT, VK_SHIFT.0)
            && key_down(chord.vk)
    }

    /// Does the foreground window belong to the game? `GetAsyncKeyState` sees keys pressed in
    /// every application, and a key typed into a browser must not open the panel.
    pub fn game_has_focus() -> bool {
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
        // The panic chord holds the default menu key and `chord_held` lets extra modifiers
        // through, so without this a press of the chord would also open or close the panel.
        let panic_chord = safety::panic_chord();
        let key = focused
            && chord_held(menu_key)
            && (menu_key == panic_chord || !chord_held(panic_chord));
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

    pub(super) fn spawn_save(settings: Settings) {
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

    pub(super) fn spawn_load() {
        let _ = std::thread::Builder::new()
            .name("er-cel-shade-load".to_owned())
            .spawn(|| {
                let (settings, warnings) = load_settings();
                if let Ok(mut slot) = FILE_RESULT.lock() {
                    *slot = Some(FileResult::Loaded(Box::new(settings), warnings));
                }
            });
    }

    /// A status light: a filled circle on the draw list, the height of a widget row.
    pub(in crate::menu) fn status_dot(ui: &Ui, light: Light) {
        let colour = match light {
            Light::Live => LIGHT_LIVE,
            Light::Busy => LIGHT_BUSY,
            Light::Error => LIGHT_ERROR,
            Light::Off => LIGHT_OFF,
        };
        let size = ui.text_line_height();
        let height = ui.frame_height();
        let at = ui.cursor_screen_pos();
        ui.get_window_draw_list()
            .add_circle(
                [at[0] + size * 0.5, at[1] + height * 0.5],
                size * 0.3,
                colour,
            )
            .filled(true)
            .build();
        ui.dummy([size, height]);
    }

    impl Menu {
        /// Once per present: stage the cel registers, the stages with content and the ports that
        /// are off for what the mode and the live settings ask for.
        fn stage(&self) {
            stage(&self.live);
        }

        pub(super) fn apply_preset(&mut self, preset: &Preset) {
            self.live.look = preset.look;
            self.live.preset.clone_from(&preset.name);
            self.preset_look = Some(preset.look);
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
                }
            }
        }

        fn edited(&self) -> bool {
            self.preset_look.is_some_and(|look| look != self.live.look)
        }

        /// The width the panel is drawn at, at 1080p.
        fn width(&self) -> f32 {
            if self.collapsed {
                STRIP_WIDTH
            } else if self.tab == Tab::Layers && self.layers.code_shown() {
                self.live.code_width
            } else {
                self.live.panel_width
            }
        }

        fn draw(&mut self, ui: &Ui, scale: f32, status: &Status) {
            let display = ui.io().display_size;
            let dock = self.live.dock;
            let docked = docked_rect(display, dock, self.width(), scale);
            let mut flags = WindowFlags::NO_TITLE_BAR
                | WindowFlags::NO_COLLAPSE
                | WindowFlags::NO_SAVED_SETTINGS;
            if docked.is_some() {
                flags |= WindowFlags::NO_MOVE | WindowFlags::NO_RESIZE;
            }
            let mut window = ui
                .window("Shaders##er-shader-editor")
                .flags(flags)
                .bg_alpha(BACKGROUND_ALPHA);
            window = match docked {
                Some(rect) => window
                    .position(rect.pos, Condition::Always)
                    .size(rect.size, Condition::Always),
                None => window
                    .position(
                        [display[0] * 0.5, display[1] * 0.1],
                        Condition::FirstUseEver,
                    )
                    .size(
                        [self.width() * scale, display[1] * 0.8],
                        Condition::FirstUseEver,
                    ),
            };
            if let Some(_window) = window.begin() {
                ui.set_window_font_scale(scale);
                if self.collapsed {
                    let arrow = if dock == Dock::Left { ">" } else { "<" };
                    if ui.small_button(arrow) {
                        self.collapsed = false;
                    }
                    if ui.is_item_hovered() {
                        let _tooltip = ui.begin_tooltip();
                        ui.text("Open the Shaders panel again.");
                    }
                } else {
                    self.draw_header(ui);
                    self.draw_toolbar(ui, status);
                    self.draw_tabs(ui, status);
                }
            }
            if let Some(panel) = docked
                && !self.collapsed
            {
                self.draw_edge(ui, display, dock, panel, scale);
            }
        }

        /// The title, the Dock combo, collapse and close.
        fn draw_header(&mut self, ui: &Ui) {
            ui.align_text_to_frame_padding();
            ui.text("Shaders");
            let spacing = ui.clone_style().item_spacing[0];
            let button = ui.frame_height();
            let combo = ui.calc_text_size("Floating")[0] + button * 2.0;
            let label = ui.calc_text_size("Dock")[0];
            let right = label + combo + button * 2.0 + spacing * 4.0;
            ui.same_line_with_pos((ui.window_content_region_max()[0] - right).max(0.0));
            ui.text_disabled("Dock");
            ui.same_line();
            ui.set_next_item_width(combo);
            if let Some(_combo) = ui.begin_combo("##dock", self.live.dock.label()) {
                for dock in Dock::ALL {
                    if ui
                        .selectable_config(dock.label())
                        .selected(dock == self.live.dock)
                        .build()
                    {
                        self.live.dock = dock;
                    }
                }
            }
            if self.live.dock != Dock::Floating {
                ui.same_line();
                let arrow = if self.live.dock == Dock::Left {
                    "<"
                } else {
                    ">"
                };
                if ui.button_with_size(arrow, [button, button]) {
                    self.collapsed = true;
                }
                if ui.is_item_hovered() {
                    let _tooltip = ui.begin_tooltip();
                    ui.text("Collapse to a strip at the edge of the screen.");
                }
            }
            ui.same_line();
            if ui.button_with_size("x", [button, button]) {
                set_open(false);
            }
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text(format!(
                    "Close. {} or Esc does the same; the game gets no input while this is open.",
                    chord_name(self.menu_key)
                ));
            }
            ui.separator();
        }

        /// Vanilla, the preset, Save and Revert, and the status line.
        fn draw_toolbar(&mut self, ui: &Ui, status: &Status) {
            self.draw_vanilla(ui);

            let presets = all_presets(&self.live);
            let shown = if self.edited() {
                format!("{} (edited)", self.live.preset)
            } else {
                self.live.preset.clone()
            };
            ui.align_text_to_frame_padding();
            ui.text_disabled("Preset");
            ui.same_line();
            let spacing = ui.clone_style().item_spacing[0];
            let buttons = ui.calc_text_size("SaveRevert")[0]
                + ui.clone_style().frame_padding[0] * 4.0
                + spacing * 3.0;
            ui.set_next_item_width((ui.content_region_avail()[0] - buttons).max(1.0));
            let mut picked = None;
            if let Some(_combo) = ui.begin_combo("##preset", &shown) {
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
            }
            if let Some(preset) = picked {
                self.apply_preset(&preset);
            }
            ui.same_line();
            let unsaved = self.live != self.saved;
            if ui.button("Save") {
                self.saved = self.live.clone();
                self.status = "Saving...".to_owned();
                spawn_save(self.live.clone());
            }
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text(format!(
                    "Writes {}: the look, the presets, the menu key and where this panel sits. \
                     Layers save themselves as you edit them.",
                    settings::FILE_NAME
                ));
            }
            ui.same_line();
            if ui.button("Revert") {
                self.live = self.saved.clone();
                self.preset_look = all_presets(&self.live)
                    .into_iter()
                    .find(|p| p.name == self.live.preset)
                    .map(|p| p.look);
                self.status = "Back to the saved settings.".to_owned();
            }
            if unsaved {
                ui.text_colored(WARNING_TEXT, "unsaved changes");
                ui.same_line();
            }
            self.draw_status_line(ui, status);
            if !self.status.is_empty() {
                ui.text_wrapped(&self.status);
            }
            ui.separator();
        }

        /// The compiler, the probe and the hooks' cost, in one line.
        fn draw_status_line(&mut self, ui: &Ui, status: &Status) {
            let present = params::presents();
            if present >= self.probe_read_at + PROBE_CHIP_PERIOD || present < self.probe_read_at {
                self.probe_read_at = present;
                self.probe_live = probe::read_latest(present).iter().any(|record| {
                    probe::frame_age(present, record.header.frame) <= PROBE_LIVE_PRESENTS
                });
            }
            ui.text_disabled(compiler_chip(&status.compiler));
            if status.in_process {
                ui.same_line();
                ui.text_colored(WARNING_TEXT, "(a compiler crash ends the game)");
            }
            ui.same_line();
            status_dot(
                ui,
                if self.probe_live {
                    Light::Live
                } else {
                    Light::Off
                },
            );
            ui.same_line();
            ui.text_disabled(if self.probe_live {
                "probe live"
            } else {
                "probe idle"
            });
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text(
                    "Live: a port variant drew in the last few frames, so the picture is the \
                     editor's. Idle: every pass is the game's own.",
                );
            }
            let timings: Vec<(u64, u64)> = Timing::ALL
                .into_iter()
                .map(|timing| {
                    (
                        cmdlist::HOOK_NS_SUM[timing as usize].load(Ordering::Relaxed),
                        cmdlist::HOOK_NS_SAMPLES[timing as usize].load(Ordering::Relaxed),
                    )
                })
                .collect();
            let floor = exports::TIMER_FLOOR_NS.load(Ordering::Relaxed);
            if let Some(ms) = hook_ms_per_frame(&timings, floor, SAMPLE_EVERY, present) {
                ui.same_line();
                ui.text_disabled(format!("hooks {ms:.2} ms/frame"));
            }
        }

        /// The Vanilla checkbox, and why the picture is vanilla when something other than the
        /// checkbox made it so.
        fn draw_vanilla(&mut self, ui: &Ui) {
            let mut vanilla = cmdlist::VANILLA.load(Ordering::Acquire);
            // Cleared while the compare key is held, vanilla stays on until the key comes up;
            // only a hook fault refuses the checkbox for the rest of the session.
            if ui.checkbox("Vanilla", &mut vanilla)
                && !safety::set_vanilla(vanilla)
                && cmdlist::HOOK_FAULTED.load(Ordering::Acquire)
            {
                self.status = "A hook caught a panic this session, so the game's own pipelines \
                               stay bound until the game restarts."
                    .to_owned();
            }
            if ui.is_item_hovered() {
                let _tooltip = ui.begin_tooltip();
                ui.text(format!(
                    "Binds the game's own pipeline for every pass, so the picture is the game's \
                     own. {PANIC_CHORD} ticks this from anywhere in the game, even with this \
                     panel closed; only this box clears it."
                ));
            }
            ui.same_line();
            ui.text_disabled("hold ` to compare, hold Tab to hide");
            if cmdlist::HOOK_FAULTED.load(Ordering::Acquire) {
                let _colour = ui.push_style_color(StyleColor::Text, ERROR_TEXT);
                ui.text_wrapped(
                    "A hook caught a panic: vanilla until the game restarts. er-cel-shade.log \
                     says where.",
                );
            }
            let setting = safety::mode_setting();
            match setting.mode {
                Mode::Normal => {}
                Mode::Vanilla => {
                    ui.text_disabled("Mode 1 (vanilla), set through the stats export.");
                }
                Mode::Fidelity => ui.text_disabled(format!(
                    "Mode 2 (fidelity test), set through the stats export: {} redirected with cel \
                     at its off values.",
                    render_ports(setting.kinds)
                )),
            }
            if self.live.unfaithful_ports != 0 {
                ui.text_wrapped(format!(
                    "Drawn as the game draws them, because the fidelity test found their cel \
                     port unfaithful: {}",
                    render_ports(self.live.unfaithful_ports)
                ));
                if ui.button("Use their cel ports again") {
                    self.live.unfaithful_ports = 0;
                    self.status =
                        "The ports are used again. Save keeps it for next time.".to_owned();
                }
            }
        }

        fn draw_tabs(&mut self, ui: &Ui, status: &Status) {
            let Some(_tabs) = ui.tab_bar("##shaders-tabs") else {
                return;
            };
            if let Some(_tab) = ui.tab_item("Layers") {
                self.tab = Tab::Layers;
                self.layers.draw(ui, status, editor_dir());
            }
            if let Some(_tab) = ui.tab_item("Look") {
                self.tab = Tab::Look;
                self.draw_look_tab(ui);
            }
            // P2's Hunt tab goes here, between Look and Diagnostics (design 9).
            if let Some(_tab) = ui.tab_item("Diagnostics") {
                self.tab = Tab::Diagnostics;
                diagnostics_tab::draw(ui, status);
            }
        }

        /// The grab strip along a docked panel's inner edge: a window of its own just outside the
        /// panel, so nothing inside the panel clips it.
        fn draw_edge(
            &mut self,
            ui: &Ui,
            display: [f32; 2],
            dock: Dock,
            panel: super::PanelRect,
            scale: f32,
        ) {
            let Some(edge) = edge_rect(panel, dock, scale) else {
                return;
            };
            // Dear ImGui asserts on an invisible button with no area, and a minimised game's
            // display can be 0 high.
            let usable = edge.size.iter().all(|side| *side >= 1.0);
            if !usable {
                return;
            }
            let padding = ui.push_style_var(StyleVar::WindowPadding([0.0, 0.0]));
            let minimum = ui.push_style_var(StyleVar::WindowMinSize([1.0, 1.0]));
            let border = ui.push_style_var(StyleVar::WindowBorderSize(0.0));
            let window = ui
                .window("##shaders-edge")
                .flags(
                    WindowFlags::NO_DECORATION
                        | WindowFlags::NO_BACKGROUND
                        | WindowFlags::NO_SAVED_SETTINGS
                        | WindowFlags::NO_FOCUS_ON_APPEARING
                        | WindowFlags::NO_NAV
                        | WindowFlags::NO_MOVE,
                )
                .position(edge.pos, Condition::Always)
                .size(edge.size, Condition::Always)
                .begin();
            drop(border);
            drop(minimum);
            drop(padding);
            let Some(_window) = window else {
                return;
            };
            ui.invisible_button("##edge", edge.size);
            let active = ui.is_item_active();
            if active || ui.is_item_hovered() {
                ui.set_mouse_cursor(Some(MouseCursor::ResizeEW));
                let x = match dock {
                    Dock::Left => edge.pos[0],
                    _ => edge.pos[0] + edge.size[0],
                };
                ui.get_window_draw_list()
                    .add_line(
                        [x, edge.pos[1]],
                        [x, edge.pos[1] + edge.size[1]],
                        LIGHT_BUSY,
                    )
                    .thickness(2.0)
                    .build();
            }
            if active
                && let Some(width) = width_from_edge(display, dock, ui.io().mouse_pos[0], scale)
            {
                if self.tab == Tab::Layers && self.layers.code_shown() {
                    self.live.code_width = width;
                } else {
                    self.live.panel_width = width;
                }
            }
        }
    }

    /// The panel's per-frame drawing, called by the overlay inside `catch_unwind`: the keys that
    /// open and close it, the keys held for compare and hide, and the panel itself while it is
    /// open. Staging and publishing are [`end_frame`]'s, which runs whatever happens here.
    pub fn draw_ui(ui: &Ui, ui_scale: f32) {
        static ARMED: AtomicBool = AtomicBool::new(false);
        if HIDDEN.load(Ordering::Acquire) {
            return;
        }
        let started = Instant::now();
        if !ARMED.load(Ordering::Relaxed) {
            // Retried each frame until DirectInput and XInput are loaded; cheap once both are.
            let dinput = input::try_install_dinput();
            let pad = input::try_install_pad();
            ARMED.store(dinput && pad, Ordering::Relaxed);
        }
        let mut slot = MENU.lock().unwrap_or_else(PoisonError::into_inner);
        let Some(menu) = slot.as_mut() else {
            return;
        };
        menu.take_file_result();
        poll_keys(menu.menu_key);
        let open = is_open();
        // Both keys are typed into a text field that has the keyboard, so they act only when
        // none has.
        let typing = ui.io().want_text_input;
        let held = |vk: u16| open && !typing && game_has_focus() && key_down(u32::from(vk));
        if let Some(compare) = menu.momentary.update(held(VK_OEM_3.0)) {
            safety::set_compare(compare);
        }
        if open && !held(VK_TAB.0) {
            let status = worker::status();
            menu.draw(ui, panel_scale(ui_scale), &status);
        }
        drop(slot);
        let micros = u64::try_from(started.elapsed().as_micros()).unwrap_or(u64::MAX);
        UI_US_LAST.store(micros, Ordering::Relaxed);
        UI_US_MAX.fetch_max(micros, Ordering::Relaxed);
    }

    /// Once per present, after [`draw_ui`] and whatever became of it: a layer change the worker
    /// has not read back is written into the parameter block, the look and the stages with
    /// content are staged, the parameter block is published with the table swaps the worker
    /// handed to this present (`crate::table::defer`), and the present is counted for the
    /// graveyard.
    ///
    /// The Layers tab's tick and the staging each run inside a `catch_unwind` of their own, and
    /// the publish runs after both whatever became of them: a panic in the tab hides the panel
    /// for the session and is never ticked again, and the look, the layers and the graveyard's
    /// clock carry on.
    pub fn end_frame(ui: &Ui) {
        static STAGE_FAILED: AtomicBool = AtomicBool::new(false);
        {
            let mut slot = MENU.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(menu) = slot.as_mut() {
                if !HIDDEN.load(Ordering::Acquire) && (is_open() || menu.layers.busy()) {
                    let status = worker::status();
                    let layers = &mut menu.layers;
                    let ticked =
                        catch_unwind(AssertUnwindSafe(|| layers.tick(&status, editor_dir())));
                    if ticked.is_err() {
                        hide_after_panic("ticking the Layers tab");
                        menu.momentary = Momentary::default();
                    }
                }
                if catch_unwind(AssertUnwindSafe(|| menu.stage())).is_err()
                    && !STAGE_FAILED.swap(true, Ordering::Relaxed)
                {
                    log(format_args!(
                        "panel: a panic while staging the look; the last staged look stays and \
                         the parameter block is still published each present (logged once)"
                    ));
                }
            }
        }
        let present = params::publish_then(ui.io().display_size, || {
            if table::apply_deferred() > 0 {
                cmdlist::refresh_active();
            }
        });
        graveyard::on_present(present);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::{DEFAULT_CODE_WIDTH, DEFAULT_PANEL_WIDTH, MAX_PANEL_WIDTH};
    use er_build_watermark_core::font_request::GLYPH_RANGES;

    #[test]
    fn the_panel_is_pinned_to_its_edge_at_its_width_times_the_scale() {
        let full_hd = [1920.0, 1080.0];
        assert_eq!(
            docked_rect(full_hd, Dock::Right, DEFAULT_PANEL_WIDTH, 1.0),
            Some(PanelRect {
                pos: [1460.0, 0.0],
                size: [460.0, 1080.0],
            })
        );
        // At 4K the panel doubles and three quarters of the frame stay clear as before.
        let uhd = [3840.0, 2160.0];
        assert_eq!(
            docked_rect(uhd, Dock::Left, DEFAULT_PANEL_WIDTH, 2.0),
            Some(PanelRect {
                pos: [0.0, 0.0],
                size: [920.0, 2160.0],
            })
        );
        assert_eq!(docked_rect(full_hd, Dock::Floating, 460.0, 1.0), None);
        // The widest saved width still leaves a fifth of the screen.
        let widest = docked_rect([1280.0, 720.0], Dock::Right, MAX_PANEL_WIDTH, 1.0);
        assert_eq!(widest.map(|rect| rect.size[0]), Some(1024.0));
        // The Code tab's width fits 100 columns of the 7-pixel default font with room to spare.
        const { assert!(DEFAULT_CODE_WIDTH >= 100.0 * 7.0) };
        assert_eq!(panel_scale(0.5), MIN_SCALE);
        assert_eq!(panel_scale(f32::NAN), 1.0);
        assert_eq!(panel_scale(2.0), 2.0);
    }

    #[test]
    fn the_inner_edge_drags_the_width() {
        let display = [1920.0, 1080.0];
        let Some(panel) = docked_rect(display, Dock::Right, 460.0, 1.0) else {
            panic!("a right dock has a rectangle");
        };
        // The grab strip is outside the panel, against its inner edge.
        assert_eq!(
            edge_rect(panel, Dock::Right, 1.0),
            Some(PanelRect {
                pos: [1460.0 - EDGE_GRAB, 0.0],
                size: [EDGE_GRAB, 1080.0],
            })
        );
        let Some(left) = docked_rect(display, Dock::Left, 460.0, 1.0) else {
            panic!("a left dock has a rectangle");
        };
        assert_eq!(
            edge_rect(left, Dock::Left, 1.0).map(|e| e.pos[0]),
            Some(460.0)
        );
        assert_eq!(edge_rect(left, Dock::Floating, 1.0), None);
        // Dragged to x = 1200 on the right: 720 wide. At 4K the same pixels are half the width.
        assert_eq!(
            width_from_edge(display, Dock::Right, 1200.0, 1.0),
            Some(720.0)
        );
        assert_eq!(
            width_from_edge([3840.0, 2160.0], Dock::Left, 1440.0, 2.0),
            Some(720.0)
        );
        // Too narrow, and past the screen's share: held to the range.
        assert_eq!(
            width_from_edge(display, Dock::Right, 1900.0, 1.0),
            Some(crate::settings::MIN_PANEL_WIDTH)
        );
        assert_eq!(
            width_from_edge(display, Dock::Left, 1900.0, 1.0),
            Some(1920.0 * MAX_SCREEN_SHARE)
        );
        assert_eq!(width_from_edge(display, Dock::Floating, 10.0, 1.0), None);
    }

    #[test]
    fn backtick_is_vanilla_only_while_it_is_held() {
        let mut key = Momentary::default();
        assert_eq!(key.update(false), None);
        assert_eq!(key.update(true), Some(true));
        // Held: nothing more to do.
        assert_eq!(key.update(true), None);
        assert_eq!(key.update(false), Some(false));
        assert_eq!(key.update(false), None);

        // Through the switches: a panic chord while the key is held survives its release.
        use crate::safety::{Event, Switches};
        let mut switches = Switches::START;
        let mut key = Momentary::default();
        if let Some(held) = key.update(true) {
            switches = switches.after(Event::Compare(held));
        }
        switches = switches.after(Event::PanicChord);
        if let Some(held) = key.update(false) {
            switches = switches.after(Event::Compare(held));
        }
        assert!(switches.vanilla(false));
    }

    #[test]
    fn the_counters_are_named_for_the_telemetry_file() {
        let names: Vec<&str> = telemetry_counters().iter().map(|(name, _)| *name).collect();
        assert!(names.contains(&"editor_edit_callbacks"));
        assert!(names.contains(&"editor_pastes"));
        let mut sorted = names.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), names.len());
        assert!(names.iter().all(|name| name.is_ascii()));
    }

    /// Is `c` drawn by the host's font? Control characters are not drawn at all.
    fn drawable(c: char) -> bool {
        let code = u32::from(c);
        c.is_control()
            || GLYPH_RANGES[..GLYPH_RANGES.len() - 1]
                .chunks(2)
                .any(|pair| (pair[0]..=pair[1]).contains(&code))
    }

    /// Every string and character literal in Rust `source`, escapes decoded, with its line.
    /// Comments are skipped; lifetimes are not literals.
    fn literals(source: &str) -> Vec<(usize, String)> {
        let chars: Vec<char> = source.chars().collect();
        let mut found = Vec::new();
        let mut line = 1;
        let mut at = 0;
        let ident = |c: char| c.is_alphanumeric() || c == '_';
        while at < chars.len() {
            let c = chars[at];
            let next = chars.get(at + 1).copied();
            let starts_token = at == 0 || !ident(chars[at - 1]);
            if c == '\n' {
                line += 1;
                at += 1;
            } else if c == '/' && next == Some('/') {
                while at < chars.len() && chars[at] != '\n' {
                    at += 1;
                }
            } else if c == '/' && next == Some('*') {
                let mut depth = 0;
                while at < chars.len() {
                    if chars[at] == '/' && chars.get(at + 1) == Some(&'*') {
                        depth += 1;
                        at += 2;
                    } else if chars[at] == '*' && chars.get(at + 1) == Some(&'/') {
                        depth -= 1;
                        at += 2;
                        if depth == 0 {
                            break;
                        }
                    } else {
                        line += usize::from(chars[at] == '\n');
                        at += 1;
                    }
                }
            } else if starts_token && (c == 'r' || (c == 'b' && next == Some('r'))) {
                // A raw string, `r"..."`, `r#"..."#`, `br"..."`, or an identifier.
                let mut probe = at + if c == 'b' { 2 } else { 1 };
                let mut hashes = 0;
                while chars.get(probe) == Some(&'#') {
                    hashes += 1;
                    probe += 1;
                }
                if chars.get(probe) != Some(&'"') {
                    at += 1;
                    continue;
                }
                let start_line = line;
                let mut text = String::new();
                probe += 1;
                while probe < chars.len() {
                    if chars[probe] == '"'
                        && (1..=hashes).all(|n| chars.get(probe + n) == Some(&'#'))
                    {
                        break;
                    }
                    line += usize::from(chars[probe] == '\n');
                    text.push(chars[probe]);
                    probe += 1;
                }
                found.push((start_line, text));
                at = probe + 1 + hashes;
            } else if c == '"' || (starts_token && c == 'b' && next == Some('"')) {
                let start_line = line;
                at += if c == 'b' { 2 } else { 1 };
                let mut text = String::new();
                while at < chars.len() && chars[at] != '"' {
                    if chars[at] == '\\' {
                        let (decoded, used, newlines) = escape(&chars[at..]);
                        text.extend(decoded);
                        line += newlines;
                        at += used;
                    } else {
                        line += usize::from(chars[at] == '\n');
                        text.push(chars[at]);
                        at += 1;
                    }
                }
                found.push((start_line, text));
                at += 1;
            } else if c == '\'' {
                if next == Some('\\') {
                    let (decoded, used, _) = escape(&chars[at + 1..]);
                    found.push((line, decoded.into_iter().collect()));
                    at += 1 + used + 1;
                } else if chars.get(at + 2) == Some(&'\'') {
                    found.push((line, chars[at + 1].to_string()));
                    at += 3;
                } else {
                    // A lifetime or a label.
                    at += 1;
                }
            } else {
                at += 1;
            }
        }
        found
    }

    /// One escape at the start of `chars`: what it stands for, the characters it took, and the
    /// line breaks it skipped (a `\` at the end of a line).
    fn escape(chars: &[char]) -> (Option<char>, usize, usize) {
        match chars.get(1) {
            Some('n') => (Some('\n'), 2, 0),
            Some('r') => (Some('\r'), 2, 0),
            Some('t') => (Some('\t'), 2, 0),
            Some('0') => (Some('\0'), 2, 0),
            Some('x') => {
                let hex: String = chars.iter().skip(2).take(2).collect();
                (
                    u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32),
                    4,
                    0,
                )
            }
            Some('u') => {
                let close = chars.iter().position(|&c| c == '}').unwrap_or(chars.len());
                let hex: String = chars[3.min(close)..close].iter().collect();
                (
                    u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32),
                    close + 1,
                    0,
                )
            }
            Some('\n') => {
                let skipped = chars[1..].iter().take_while(|c| c.is_whitespace()).count();
                let newlines = chars[1..1 + skipped].iter().filter(|&&c| c == '\n').count();
                (None, 1 + skipped, newlines)
            }
            Some(&other) => (Some(other), 2, 0),
            None => (None, 1, 0),
        }
    }

    #[test]
    fn the_literal_scanner_finds_what_it_must() {
        let arrow = char::from_u32(0x2192).map_or_else(String::new, String::from);
        let source = format!(
            "// a comment with {arrow} in it is never drawn\n\
             fn f<'a>(x: &'a str) -> char {{ let _ = \"plain {arrow}\"; let _ = r#\"raw {arrow}\"#;\n\
             let _ = \"\\u{{2192}} and caf\\u{{e9}}\"; let _ = 'x'; let _ = '\\n'; '{arrow}' }}\n\
             /* a block {arrow} /* nested */ comment */ let _ = \"a \\\n   continued\";\n"
        );
        let found = literals(&source);
        let texts: Vec<&str> = found.iter().map(|(_, text)| text.as_str()).collect();
        let decoded = format!("{arrow} and caf\u{e9}");
        assert_eq!(
            texts,
            vec![
                format!("plain {arrow}").as_str(),
                format!("raw {arrow}").as_str(),
                decoded.as_str(),
                "x",
                "\n",
                arrow.as_str(),
                "a continued",
            ]
        );
        assert_eq!(found[0].0, 2);
        assert_eq!(found[6].0, 4);
        let refused: Vec<&str> = texts
            .iter()
            .copied()
            .filter(|text| !text.chars().all(drawable))
            .collect();
        assert_eq!(refused.len(), 4, "{refused:?}");
        assert!(drawable('\u{e9}') && drawable('\u{2014}') && drawable('\n'));
        assert!(!drawable('\u{2192}') && !drawable('\u{1f600}'));
    }

    #[test]
    fn every_string_the_panel_writes_is_in_the_hosts_glyph_ranges() {
        let folder = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/menu");
        let mut files = 0;
        let mut refused = Vec::new();
        let Ok(entries) = std::fs::read_dir(&folder) else {
            panic!("{} cannot be listed", folder.display());
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("rs") {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&path) else {
                panic!("{} cannot be read", path.display());
            };
            files += 1;
            // The tests' own strings are never drawn.
            let code = text.split("\n#[cfg(test)]").next().unwrap_or(&text);
            for (line, literal) in literals(code) {
                for c in literal.chars().filter(|&c| !drawable(c)) {
                    refused.push(format!(
                        "{}:{line}: U+{:04X} in {literal:?}",
                        path.display(),
                        u32::from(c)
                    ));
                }
            }
        }
        assert!(files >= 7, "only {files} files under {}", folder.display());
        assert!(
            refused.is_empty(),
            "drawn as boxes:\n{}",
            refused.join("\n")
        );
    }
}
