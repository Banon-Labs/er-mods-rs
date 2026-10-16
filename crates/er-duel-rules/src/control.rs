//! The rules and settings in force, and the two files they live in: `er-duel-rules.json` (the
//! rules every referee shares) and `er-duel-rules.toml` (this DLL's keys and whether the list
//! shows).
//!
//! Both are read at attach. A worker thread then, once a second, writes whatever the menu changed
//! or else picks up an edit made to either file by something else -- the Cheat Engine table, the
//! Frida agent, `scripts/er-duel-rules-bans.py`, a text editor. The game task asks for the rules
//! only when [`rules_generation`] has moved, so the referee is never handed a half-made edit.
//!
//! The rules file is written only once the menu changes something: with no file every referee
//! applies the ruleset as written, and a file the player never asked for would say otherwise to
//! the next person reading the folder.

#![cfg(windows)]

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, PoisonError};

use er_duel_rules_core::config::{Config, RULES_FILE_NAME, read_config, write_config};
use er_hotkey_config::{FileChange, HotFile, write_atomic};

use crate::log::rules_log;
use crate::settings::{self, Settings};

static RULES: Mutex<Option<Config>> = Mutex::new(None);
/// Moves every time the rules in force change.
static RULES_GENERATION: AtomicU64 = AtomicU64::new(0);
static RULES_HOT: Mutex<Option<HotFile>> = Mutex::new(None);
/// What the last read of the rules file could not use.
static RULES_PROBLEMS: Mutex<Vec<String>> = Mutex::new(Vec::new());
static RULES_SAVE_PENDING: AtomicBool = AtomicBool::new(false);

static SETTINGS: Mutex<Option<Settings>> = Mutex::new(None);
static SETTINGS_HOT: Mutex<Option<HotFile>> = Mutex::new(None);
static SETTINGS_WARNINGS: Mutex<Vec<String>> = Mutex::new(Vec::new());
/// The settings file did not exist at attach, so the worker writes the defaults once.
static SETTINGS_WRITE_DEFAULTS: AtomicBool = AtomicBool::new(false);
static SHOW_BREAKS_SAVE_PENDING: AtomicBool = AtomicBool::new(false);
/// Moves every time the keys in force change, so the menu re-reads them.
static SETTINGS_GENERATION: AtomicU64 = AtomicU64::new(0);
static SHOW_BREAKS: AtomicBool = AtomicBool::new(true);

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn beside_game(name: &str) -> PathBuf {
    er_game_base::log::game_directory_path()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(name)
}

pub fn rules_path() -> PathBuf {
    beside_game(RULES_FILE_NAME)
}

pub fn settings_path() -> PathBuf {
    beside_game(settings::FILE_NAME)
}

/// Read both files, or start from the ruleset as written and the default keys.
pub fn init() {
    let path = rules_path();
    let mut hot = HotFile::new(&path);
    match std::fs::read_to_string(&path) {
        Ok(text) => {
            adopt_rules_text(&text, "read");
            hot.adopt(text);
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            *lock(&RULES) = Some(Config::default());
            rules_log(format_args!(
                "rules: no {} -- the ruleset as written",
                path.display()
            ));
        }
        Err(error) => {
            *lock(&RULES) = Some(Config::default());
            rules_log(format_args!(
                "rules: could not read {}: {error}; the ruleset as written",
                path.display()
            ));
        }
    }
    *lock(&RULES_HOT) = Some(hot);
    RULES_GENERATION.fetch_add(1, Ordering::AcqRel);

    let path = settings_path();
    let mut hot = HotFile::new(&path);
    let (loaded, warnings) = match std::fs::read_to_string(&path) {
        Ok(text) => {
            let parsed = settings::parse(&text);
            hot.adopt(text);
            parsed
        }
        Err(error) => {
            if error.kind() == std::io::ErrorKind::NotFound {
                SETTINGS_WRITE_DEFAULTS.store(true, Ordering::Release);
            }
            (Settings::default(), Vec::new())
        }
    };
    *lock(&SETTINGS_HOT) = Some(hot);
    adopt_settings(loaded, warnings, "read");
}

fn adopt_rules_text(text: &str, how: &str) {
    let (config, problems) = read_config(text);
    for problem in &problems {
        rules_log(format_args!("rules: {problem}"));
    }
    rules_log(format_args!(
        "rules: {how} {}: {}",
        RULES_FILE_NAME,
        config.describe()
    ));
    *lock(&RULES_PROBLEMS) = problems;
    *lock(&RULES) = Some(config);
}

fn adopt_settings(loaded: Settings, warnings: Vec<String>, how: &str) {
    for warning in &warnings {
        rules_log(format_args!("{}: {warning}", settings::FILE_NAME));
    }
    rules_log(format_args!(
        "settings: {how}: menu key {}, list key {}, list {}",
        loaded.menu_key,
        loaded.breaks_key,
        if loaded.show_breaks {
            "shown"
        } else {
            "hidden"
        }
    ));
    SHOW_BREAKS.store(loaded.show_breaks, Ordering::Release);
    *lock(&SETTINGS_WARNINGS) = warnings;
    *lock(&SETTINGS) = Some(loaded);
    SETTINGS_GENERATION.fetch_add(1, Ordering::AcqRel);
}

/// The rules in force.
#[must_use]
pub fn rules() -> Config {
    lock(&RULES).clone().unwrap_or_default()
}

#[must_use]
pub fn rules_generation() -> u64 {
    RULES_GENERATION.load(Ordering::Acquire)
}

/// What the rules file held that could not be used.
#[must_use]
pub fn rules_problems() -> Vec<String> {
    lock(&RULES_PROBLEMS).clone()
}

/// Change the rules in force now; the worker saves them within a second.
pub fn edit_rules(edit: impl FnOnce(&mut Config)) {
    let mut slot = lock(&RULES);
    let config = slot.get_or_insert_with(Config::default);
    let before = config.clone();
    edit(config);
    if *config == before {
        return;
    }
    let described = config.describe();
    drop(slot);
    lock(&RULES_PROBLEMS).clear();
    RULES_GENERATION.fetch_add(1, Ordering::AcqRel);
    RULES_SAVE_PENDING.store(true, Ordering::Release);
    rules_log(format_args!("rules: changed in the menu: {described}"));
}

/// The settings in force.
#[must_use]
pub fn settings() -> Settings {
    lock(&SETTINGS).clone().unwrap_or_default()
}

#[must_use]
pub fn settings_generation() -> u64 {
    SETTINGS_GENERATION.load(Ordering::Acquire)
}

#[must_use]
pub fn settings_warnings() -> Vec<String> {
    lock(&SETTINGS_WARNINGS).clone()
}

/// Is the rule-break list drawn?
#[must_use]
pub fn show_breaks() -> bool {
    SHOW_BREAKS.load(Ordering::Acquire)
}

/// Show or hide the rule-break list; saved within a second. Breaks are filed either way.
pub fn set_show_breaks(show: bool) {
    if SHOW_BREAKS.swap(show, Ordering::AcqRel) == show {
        return;
    }
    if let Some(s) = lock(&SETTINGS).as_mut() {
        s.show_breaks = show;
    }
    SHOW_BREAKS_SAVE_PENDING.store(true, Ordering::Release);
    rules_log(format_args!(
        "settings: rule-break list {} (breaks are still filed and logged)",
        if show { "shown" } else { "hidden" }
    ));
}

/// Put new keys in force and in the file, or say why not.
pub fn set_keys(menu_key: &str, breaks_key: &str) -> Result<(), String> {
    let menu = er_hotkey_config::parse_chord(menu_key)
        .map_err(|error| format!("the menu key `{menu_key}` is not a key ({error})"))?;
    let breaks = er_hotkey_config::parse_chord(breaks_key)
        .map_err(|error| format!("the list key `{breaks_key}` is not a key ({error})"))?;
    if menu == breaks {
        return Err("the two keys must differ".to_owned());
    }
    let mut next = settings();
    next.menu_key = menu_key.trim().to_owned();
    next.breaks_key = breaks_key.trim().to_owned();
    let path = settings_path();
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => settings::with_key(
            &settings::with_key(&text, "menu_key", &next.menu_key),
            "breaks_key",
            &next.breaks_key,
        ),
        Err(_) => settings::render(&next),
    };
    write_atomic(&path, &text).map_err(|error| format!("saving {}: {error}", path.display()))?;
    if let Some(hot) = lock(&SETTINGS_HOT).as_mut() {
        hot.adopt(text);
    }
    adopt_settings(next, Vec::new(), "keys changed in the menu");
    Ok(())
}

/// Once a second, on the worker: save what the menu changed, else pick up an edit to either file.
pub fn tick() {
    tick_rules();
    tick_settings();
}

fn tick_rules() {
    let mut hot_slot = lock(&RULES_HOT);
    let Some(hot) = hot_slot.as_mut() else {
        return;
    };
    let path = rules_path();
    if RULES_SAVE_PENDING.swap(false, Ordering::AcqRel) {
        let text = write_config(&rules());
        match write_atomic(&path, &text) {
            Ok(()) => {
                hot.adopt(text);
                rules_log(format_args!("rules: saved to {}", path.display()));
            }
            Err(error) => rules_log(format_args!(
                "rules: saving {} failed: {error}; the rules stay in force for this session",
                path.display()
            )),
        }
        return;
    }
    match hot.poll() {
        Some(FileChange::Text(text)) => {
            drop(hot_slot);
            adopt_rules_text(&text, "reread after an edit to");
            RULES_GENERATION.fetch_add(1, Ordering::AcqRel);
        }
        Some(FileChange::Missing) => {
            drop(hot_slot);
            if *lock(&RULES) != Some(Config::default()) {
                rules_log(format_args!(
                    "rules: {} was removed -- the ruleset as written",
                    RULES_FILE_NAME
                ));
                *lock(&RULES) = Some(Config::default());
                lock(&RULES_PROBLEMS).clear();
                RULES_GENERATION.fetch_add(1, Ordering::AcqRel);
            }
        }
        _ => {}
    }
}

fn tick_settings() {
    let mut hot_slot = lock(&SETTINGS_HOT);
    let Some(hot) = hot_slot.as_mut() else {
        return;
    };
    let path = settings_path();
    let write_defaults = SETTINGS_WRITE_DEFAULTS.swap(false, Ordering::AcqRel);
    if write_defaults || SHOW_BREAKS_SAVE_PENDING.swap(false, Ordering::AcqRel) {
        let text = match std::fs::read_to_string(&path) {
            Ok(text) => settings::with_show_breaks(&text, show_breaks()),
            Err(_) => settings::render(&settings()),
        };
        match write_atomic(&path, &text) {
            Ok(()) => {
                hot.adopt(text);
                rules_log(format_args!("settings: saved to {}", path.display()));
            }
            Err(error) => rules_log(format_args!(
                "settings: saving {} failed: {error}",
                path.display()
            )),
        }
        return;
    }
    if let Some(FileChange::Text(text)) = hot.poll() {
        drop(hot_slot);
        let (loaded, warnings) = settings::parse(&text);
        adopt_settings(loaded, warnings, "reread after an edit");
    }
}
