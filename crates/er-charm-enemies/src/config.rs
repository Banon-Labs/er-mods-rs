//! `er-charm-enemies.toml`, re-read while the game runs.
//!
//! Hand-parsed rather than pulled through a TOML crate: four scalar settings do not justify a
//! dependency in a DLL that is cross-compiled into the game process.
//!
//! # Why it reloads
//!
//! The hotkey was read ONCE, at attach, into a `OnceLock`. Changing it meant quitting Elden Ring,
//! which is a long way to go to find out you picked a key another mod had already taken -- and
//! finding that out is exactly what makes a player want to change it. The file is now re-read about
//! once a second (`er_hotkey_config::HotFile`, which compares the file's TEXT so a fast edit cannot
//! fall inside one mtime tick) and the new key is live on the next keyboard poll.
//!
//! Two rules the reload has to get right, both of them ways a "working" reload looks broken:
//!
//! * A key that moved RESETS the edge detector. Without that, a key held at the instant of the swap
//!   is already latched as down, and releasing it -- or the very next poll -- reads as a fresh
//!   press the player never made.
//! * A key that did NOT move must not report a change, or every reformatting of the file produces
//!   that same phantom press.

// Windows-only in practice; kept portable so the parser and the reload decision below are covered
// by `cargo test` on the host, where the windows-gated modules that consume them are compiled out.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{
    fs,
    path::PathBuf,
    sync::{Mutex, MutexGuard, OnceLock},
};

use er_hotkey_config::{Binding, BindingUpdate, FileChange, HotFile, chord_name};

use crate::{
    keys::{Chord, parse_scancode_chord},
    log::charm_log,
};

const CONFIG_FILE_NAME: &str = "er-charm-enemies.toml";

/// SpEffect 20503350, `[Item] Charming Branch` in `SpEffectParam`.
///
/// Its `stateInfo` is 132 (`0x84`), and that number is the whole charm mechanism:
/// `CS::ChrIns::GetTeamType` (`0x1403f1a60`, 1.16.2) reports `TeamType::Charmed` for any character
/// whose `SpecialEffect` container holds an effect with that state, and reports the character's own
/// `teamType` otherwise. Nothing else gates it -- there is no `enableCharm` check on that path --
/// so putting this row on a `ChrIns` is exactly what the thrown item does to whatever it hits.
///
/// The base-game `Bewitching Branch` (503350) is the same 180-second `stateInfo` 132 effect and
/// works identically here.
pub(crate) const DEFAULT_EFFECT_ID: i32 = 20503350;

const DEFAULT_HOTKEY: &str = "ctrl+alt+c";

const DEFAULT_CONFIG_TOML: &str = r#"# er-charm-enemies standalone DLL configuration.
#
# Press the hotkey in-game to toggle "charm every loaded enemy" on and off. While it is on the
# DLL re-applies the effect to any loaded enemy that is not currently under it, so newly spawned
# enemies are charmed as they appear and the 180-second duration never lapses.
#
# EDITS TAKE EFFECT IMMEDIATELY. This file is re-read about once a second while the game runs;
# there is no need to restart. The log names the old key and the new one each time it moves.
#
# Modifiers: ctrl, alt, shift (either side of the keyboard counts). One trigger key, by name:
#
#   A..Z, 0..9, F1..F15
#   Insert Delete Home End PageUp PageDown Backspace Tab Enter Escape Space
#   Left Up Right Down PrintScreen ScrollLock NumLock Pause CapsLock
#   punctuation by symbol or name: - = [ ] \ ; ' , . / `  (Minus, Equals, LeftBracket,
#     RightBracket, Backslash, Semicolon, Quote, Comma, Period, Slash, Grave)
#   keypad: KP_0..KP_9, KP_Plus, KP_Minus, KP_Multiply, KP_Divide, KP_Period, KP_Enter
#
# Case and spacing do not matter. A key this file does not recognise is reported in the log and
# THE PREVIOUS KEY STAYS IN FORCE -- a typo never leaves you with no hotkey at all.
hotkey = "ctrl+alt+c"
# SpEffectParam row to apply. 20503350 is Charming Branch; 503350 is Bewitching Branch, the
# base-game item with the same charm state and duration.
effect_id = 20503350
# Strip the effect from every charmed enemy when the hotkey toggles the feature back off. With
# this false the charm instead lapses on its own, up to 180 seconds later.
remove_on_disable = true
"#;

/// The settings in force, and the machinery that keeps them current.
///
/// Deliberately free of Windows types so the whole reload decision is `cargo test`-able: the game
/// side of this crate consumes [`CharmConfig::hotkey`] and nothing else.
#[derive(Clone, Debug)]
pub(crate) struct CharmConfig {
    pub(crate) config_path: PathBuf,
    hotkey: Binding<Chord>,
    pub(crate) hotkey_text: String,
    pub(crate) effect_id: i32,
    pub(crate) remove_on_disable: bool,
}

/// What re-reading the file did, in the terms the log line needs.
///
/// `Default` is "nothing moved", which is what almost every poll produces.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ConfigUpdate {
    /// `(old name, new name)` when the hotkey moved. THIS is the edge-reset signal.
    pub(crate) hotkey_moved: Option<(String, String)>,
    /// The value that could not be read, and what is still in force instead.
    pub(crate) hotkey_rejected: Option<(String, String)>,
    pub(crate) effect_id_moved: Option<(i32, i32)>,
    pub(crate) remove_on_disable_moved: Option<(bool, bool)>,
}

impl ConfigUpdate {
    /// Did anything move? A poll that changed nothing must produce no log line at all.
    pub(crate) const fn is_quiet(&self) -> bool {
        self.hotkey_moved.is_none()
            && self.hotkey_rejected.is_none()
            && self.effect_id_moved.is_none()
            && self.remove_on_disable_moved.is_none()
    }
}

fn default_hotkey() -> Chord {
    parse_scancode_chord(DEFAULT_HOTKEY).expect("the built-in default hotkey parses")
}

impl CharmConfig {
    fn new(path: PathBuf) -> Self {
        Self {
            config_path: path,
            hotkey: Binding::new(default_hotkey()),
            hotkey_text: DEFAULT_HOTKEY.to_owned(),
            effect_id: DEFAULT_EFFECT_ID,
            remove_on_disable: true,
        }
    }

    pub(crate) fn hotkey(&self) -> Chord {
        self.hotkey.code()
    }

    /// Apply one file's text to the settings in force.
    ///
    /// Absent keys keep what is already in force -- which for the FIRST load is the built-in
    /// default, and for a reload is whatever was last read successfully. That is what makes
    /// deleting a line the same as never having written it.
    pub(crate) fn apply(&mut self, text: &str) -> ConfigUpdate {
        let mut update = ConfigUpdate::default();

        if let Some(raw) = setting(text, "hotkey").filter(|value| !value.is_empty()) {
            let before = chord_name(self.hotkey.code());
            match self.hotkey.apply(raw, parse_scancode_chord) {
                BindingUpdate::Unchanged => {
                    // Still record the spelling the player used, so the status line echoes their
                    // file rather than the last spelling of the same key.
                    self.hotkey_text = raw.to_owned();
                }
                BindingUpdate::Changed { .. } => {
                    self.hotkey_text = raw.to_owned();
                    update.hotkey_moved = Some((before, chord_name(self.hotkey.code())));
                }
                BindingUpdate::Rejected { value, error, kept } => {
                    update.hotkey_rejected =
                        Some((format!("{value:?}: {error}"), chord_name(kept)));
                }
            }
        }

        if let Some(effect_id) = setting(text, "effect_id").and_then(|v| v.parse::<i32>().ok())
            && effect_id != self.effect_id
        {
            update.effect_id_moved = Some((self.effect_id, effect_id));
            self.effect_id = effect_id;
        }

        if let Some(raw) = setting(text, "remove_on_disable") {
            let remove = raw != "false";
            if remove != self.remove_on_disable {
                update.remove_on_disable_moved = Some((self.remove_on_disable, remove));
                self.remove_on_disable = remove;
            }
        }

        update
    }
}

/// The live settings plus the file watcher that keeps them current.
struct ConfigState {
    config: CharmConfig,
    hot: HotFile,
}

static CONFIG: OnceLock<Mutex<ConfigState>> = OnceLock::new();

fn config_path() -> PathBuf {
    PathBuf::from(CONFIG_FILE_NAME)
}

/// Strip an inline `#` comment and surrounding whitespace/quotes from a value.
fn scalar(raw: &str) -> &str {
    let value = match raw.split_once('#') {
        Some((before, _)) => before,
        None => raw,
    }
    .trim();
    value.trim_matches('"')
}

fn setting<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.starts_with('#'))
        .find_map(|line| {
            let (name, value) = line.split_once('=')?;
            (name.trim() == key).then(|| scalar(value))
        })
}

fn parse_config(text: &str, path: PathBuf) -> CharmConfig {
    let mut config = CharmConfig::new(path);
    let update = config.apply(text);
    if let Some((rejected, kept)) = update.hotkey_rejected {
        charm_log(format_args!("config: hotkey {rejected}; staying on {kept}"));
    }
    config
}

fn state() -> MutexGuard<'static, ConfigState> {
    let state = CONFIG.get_or_init(|| {
        let path = config_path();
        let text = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(_) => {
                let _ = fs::write(&path, DEFAULT_CONFIG_TOML);
                DEFAULT_CONFIG_TOML.to_owned()
            }
        };
        let mut hot = HotFile::new(path.clone());
        // Adopt what we just read, so the first poll a second from now is not a spurious reload
        // of text nothing has touched.
        hot.adopt(text.clone());
        Mutex::new(ConfigState {
            config: parse_config(&text, path),
            hot,
        })
    });
    // A poisoned lock here means a previous holder panicked; the settings inside are still a valid
    // config, and refusing to read them would disable the feature over a fault that already
    // happened.
    match state.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

/// Read the config, writing the commented default file when there is not one yet.
pub(crate) fn init_config() -> CharmConfig {
    state().config.clone()
}

/// The settings in force right now.
pub(crate) fn config() -> CharmConfig {
    state().config.clone()
}

/// The hotkey in force, without cloning the paths and strings around it.
///
/// This is on the per-frame path, so it takes the lock and copies 8 bytes rather than allocating.
pub(crate) fn live_hotkey() -> Chord {
    state().config.hotkey()
}

/// Re-read the file if it changed, and report what moved.
///
/// Returns `None` when nothing happened -- which is the overwhelmingly common case, and the one
/// that must stay silent in the log. `Some` carries a report the caller logs AND acts on: a moved
/// hotkey is the signal to reset the key edge state.
pub(crate) fn poll_reload() -> Option<ConfigUpdate> {
    let mut guard = state();
    let ConfigState { config, hot } = &mut *guard;
    match hot.poll()? {
        FileChange::Text(text) => {
            let update = config.apply(&text);
            (!update.is_quiet()).then_some(update)
        }
        FileChange::Missing => {
            // The file was deleted. Keep the settings that were working -- a deleted config is
            // not an instruction to unbind the hotkey, and re-writing the default file here would
            // fight a player who is mid-edit.
            charm_log(format_args!(
                "config: {} disappeared; keeping the settings already in force",
                config.config_path.display()
            ));
            None
        }
    }
}

/// Log what a reload changed. Split from [`poll_reload`] so the decision stays host-testable.
pub(crate) fn log_update(update: &ConfigUpdate) {
    if let Some((from, to)) = &update.hotkey_moved {
        charm_log(format_args!("config: hotkey {from} -> {to}"));
    }
    if let Some((rejected, kept)) = &update.hotkey_rejected {
        charm_log(format_args!("config: hotkey {rejected}; staying on {kept}"));
    }
    if let Some((from, to)) = update.effect_id_moved {
        charm_log(format_args!("config: effect_id {from} -> {to}"));
    }
    if let Some((from, to)) = update.remove_on_disable_moved {
        charm_log(format_args!("config: remove_on_disable {from} -> {to}"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use er_hotkey_config::keys::{MODIFIER_ALT, MODIFIER_CTRL};

    fn parse(text: &str) -> CharmConfig {
        let mut config = CharmConfig::new(PathBuf::from("test.toml"));
        config.apply(text);
        config
    }

    #[test]
    fn the_shipped_default_file_parses_to_the_documented_defaults() {
        let parsed = parse(DEFAULT_CONFIG_TOML);
        assert_eq!(parsed.hotkey_text, DEFAULT_HOTKEY);
        assert_eq!(parsed.hotkey().modifiers, MODIFIER_CTRL | MODIFIER_ALT);
        assert_eq!(parsed.effect_id, DEFAULT_EFFECT_ID);
        assert!(parsed.remove_on_disable);
    }

    #[test]
    fn an_empty_file_still_yields_working_defaults() {
        let parsed = parse("");
        assert_eq!(parsed.effect_id, DEFAULT_EFFECT_ID);
        assert_eq!(parsed.hotkey_text, DEFAULT_HOTKEY);
        assert!(parsed.remove_on_disable);
    }

    #[test]
    fn settings_override_the_defaults() {
        let parsed =
            parse("hotkey = \"shift+f9\"\neffect_id = 503350\nremove_on_disable = false\n");
        assert_eq!(parsed.hotkey_text, "shift+f9");
        assert_eq!(parsed.effect_id, 503350);
        assert!(!parsed.remove_on_disable);
    }

    #[test]
    fn a_commented_out_setting_is_not_a_setting() {
        let parsed = parse("# effect_id = 1\neffect_id = 503350 # trailing comment\n");
        assert_eq!(parsed.effect_id, 503350);
    }

    /// RELOAD, the whole point: a changed key is picked up, and reported so the caller can reset
    /// its edge state and the log can name both ends.
    #[test]
    fn a_changed_hotkey_is_picked_up_and_names_both_ends() {
        let mut config = parse(DEFAULT_CONFIG_TOML);
        let update = config.apply("hotkey = \"ctrl+alt+v\"\n");
        assert_eq!(
            update.hotkey_moved,
            Some(("Ctrl+Alt+C".to_owned(), "Ctrl+Alt+V".to_owned()))
        );
        assert_eq!(config.hotkey().dik, Some(0x2f), "DIK_V");
        assert!(!update.is_quiet());
    }

    /// RELOAD: an unchanged file must not churn. A reported change resets the key edge detector,
    /// and a reset while the key is held reads as a press the player never made -- so re-applying
    /// identical text, or the same key spelled differently, must stay silent.
    #[test]
    fn an_unchanged_file_does_not_churn() {
        let mut config = parse(DEFAULT_CONFIG_TOML);
        for text in [
            DEFAULT_CONFIG_TOML,
            "hotkey = \"ctrl+alt+c\"\n",
            "hotkey = \"CTRL + ALT + C\"\n",
            "hotkey = \"Ctrl+Alt+C\"\neffect_id = 20503350\nremove_on_disable = true\n",
        ] {
            let update = config.apply(text);
            assert!(update.is_quiet(), "{text:?} reported {update:?}");
        }
    }

    /// RELOAD: a malformed value falls back to the PREVIOUS working key, not to the built-in
    /// default and not to nothing. A typo must leave the player with a hotkey that still works and
    /// a log line naming the typo.
    #[test]
    fn a_malformed_hotkey_falls_back_to_the_previous_value_not_to_nothing() {
        let mut config = parse(DEFAULT_CONFIG_TOML);
        config.apply("hotkey = \"shift+f9\"\n");
        let working = config.hotkey();
        assert_eq!(working.dik, Some(0x43), "DIK_F9");

        let update = config.apply("hotkey = \"ctrl+alt+nonsense\"\n");
        assert_eq!(config.hotkey(), working, "the working key stays in force");
        assert!(update.hotkey_moved.is_none(), "a rejection is not a move");
        let (rejected, kept) = update.hotkey_rejected.expect("the typo is reported");
        assert!(rejected.contains("nonsense"), "{rejected}");
        assert_eq!(kept, "Shift+F9");
    }

    /// A key with no DirectInput scancode can never appear in the buffer this DLL reads, so it is
    /// refused the same way a typo is rather than becoming a hotkey that silently never fires.
    #[test]
    fn a_key_this_dll_could_never_see_is_refused_like_a_typo() {
        let mut config = parse(DEFAULT_CONFIG_TOML);
        let update = config.apply("hotkey = \"F16\"\n");
        assert!(update.hotkey_rejected.is_some());
        assert_eq!(config.hotkey(), default_hotkey());
    }

    /// A line the file no longer carries keeps whatever was in force. Deleting a setting is the
    /// same as never having written it, not an instruction to unbind.
    #[test]
    fn a_removed_line_keeps_the_setting_in_force() {
        let mut config = parse("hotkey = \"f9\"\neffect_id = 503350\n");
        let update = config.apply("# everything commented out\n");
        assert!(update.is_quiet());
        assert_eq!(config.hotkey().dik, Some(0x43));
        assert_eq!(config.effect_id, 503350);
    }

    /// The other settings reload too, and each names both ends.
    #[test]
    fn the_other_settings_reload_and_report_both_ends() {
        let mut config = parse(DEFAULT_CONFIG_TOML);
        let update = config.apply("effect_id = 503350\nremove_on_disable = false\n");
        assert_eq!(update.effect_id_moved, Some((DEFAULT_EFFECT_ID, 503350)));
        assert_eq!(update.remove_on_disable_moved, Some((true, false)));
    }
}
