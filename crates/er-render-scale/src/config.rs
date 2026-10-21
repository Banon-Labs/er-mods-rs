//! The settings in force, and `er-render-scale.toml` beside the game: read at load, written by the
//! worker when the slider moves, and reread when its text changes.
//!
//! The divisor saved in the file is applied at boot: [`init`] hands it to the hook before the game
//! makes its draw context, and the hook puts it in force on the first update after the screen
//! allows it.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, PoisonError};

use er_hotkey_config::{FileChange, HotFile, write_atomic};

use crate::report::SettingsState;
use crate::settings::{self, Settings};
use crate::{drs, log, menu};

/// The settings in force.
static LIVE: Mutex<Option<Settings>> = Mutex::new(None);
/// The warnings the last read of the file raised.
static WARNINGS: Mutex<Vec<String>> = Mutex::new(Vec::new());
/// The file, watched by content.
static HOT: Mutex<Option<HotFile>> = Mutex::new(None);
/// The divisor changed since the file was last written.
static SAVE_PENDING: AtomicBool = AtomicBool::new(false);
/// The file did not exist at load, so the worker writes the defaults once.
static WRITE_DEFAULTS: AtomicBool = AtomicBool::new(false);

static SAVES: AtomicU64 = AtomicU64::new(0);
static SAVE_ERRORS: AtomicU64 = AtomicU64::new(0);
static RELOADS: AtomicU64 = AtomicU64::new(0);

/// The settings file's path, beside the game executable.
#[must_use]
pub fn path() -> PathBuf {
    er_game_base::log::game_directory_path()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(settings::FILE_NAME)
}

/// Read the file, or the defaults when there is none, and put the result in force: the menu key
/// to the menu, the divisor to the hook.
pub fn init() {
    let path = path();
    let (settings, warnings, text) = match std::fs::read_to_string(&path) {
        Ok(text) => {
            let (settings, warnings) = settings::parse(&text);
            (settings, warnings, Some(text))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            WRITE_DEFAULTS.store(true, Ordering::Release);
            (Settings::default(), Vec::new(), None)
        }
        Err(error) => (
            Settings::default(),
            vec![format!("could not read {}: {error}", settings::FILE_NAME)],
            None,
        ),
    };
    for warning in &warnings {
        log(format_args!("{}: {warning}", settings::FILE_NAME));
    }
    log(format_args!(
        "settings: menu key {} divisor {} ({})",
        settings.menu_key,
        settings.divisor,
        path.display()
    ));
    apply(&settings);
    let mut hot = HotFile::new(&path);
    if let Some(text) = text {
        hot.adopt(text);
    }
    *HOT.lock().unwrap_or_else(PoisonError::into_inner) = Some(hot);
    *WARNINGS.lock().unwrap_or_else(PoisonError::into_inner) = warnings;
    *LIVE.lock().unwrap_or_else(PoisonError::into_inner) = Some(settings);
}

fn apply(settings: &Settings) {
    drs::select(settings.divisor);
    menu::set_menu_key(&settings.menu_key);
}

/// The settings in force.
#[must_use]
pub fn live() -> Settings {
    LIVE.lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clone()
        .unwrap_or_default()
}

/// The slider moved: put `divisor` in force now, and save it on the worker's next tick.
pub fn set_divisor(divisor: u32) {
    let mut slot = LIVE.lock().unwrap_or_else(PoisonError::into_inner);
    let settings = slot.get_or_insert_with(Settings::default);
    if settings.divisor == divisor {
        return;
    }
    settings.divisor = divisor;
    drop(slot);
    drs::select(divisor);
    SAVE_PENDING.store(true, Ordering::Release);
}

/// Once a second, on the worker: write a pending divisor, or the defaults for a file that was not
/// there, else pick up an edit made to the file by hand.
pub fn tick() {
    let path = path();
    let mut hot_slot = HOT.lock().unwrap_or_else(PoisonError::into_inner);
    let Some(hot) = hot_slot.as_mut() else {
        return;
    };
    if WRITE_DEFAULTS.swap(false, Ordering::AcqRel) || SAVE_PENDING.swap(false, Ordering::AcqRel) {
        let settings = live();
        let text = match std::fs::read_to_string(&path) {
            Ok(text) => settings::with_divisor(&text, settings.divisor),
            Err(_) => settings::render(&settings),
        };
        match write_atomic(&path, &text) {
            Ok(()) => {
                SAVES.fetch_add(1, Ordering::Relaxed);
                hot.adopt(text);
                log(format_args!(
                    "settings: divisor {} saved to {}",
                    settings.divisor,
                    path.display()
                ));
            }
            Err(error) => {
                SAVE_ERRORS.fetch_add(1, Ordering::Relaxed);
                log(format_args!(
                    "settings: save to {} failed: {error}",
                    path.display()
                ));
            }
        }
        return;
    }
    let Some(FileChange::Text(text)) = hot.poll() else {
        return;
    };
    drop(hot_slot);
    let (settings, warnings) = settings::parse(&text);
    for warning in &warnings {
        log(format_args!("{}: {warning}", settings::FILE_NAME));
    }
    log(format_args!(
        "settings: reread after an edit: menu key {} divisor {}",
        settings.menu_key, settings.divisor
    ));
    RELOADS.fetch_add(1, Ordering::Relaxed);
    apply(&settings);
    *WARNINGS.lock().unwrap_or_else(PoisonError::into_inner) = warnings;
    *LIVE.lock().unwrap_or_else(PoisonError::into_inner) = Some(settings);
}

/// The file's part of the telemetry.
#[must_use]
pub fn state() -> SettingsState {
    SettingsState {
        path: path().display().to_string(),
        divisor: live().divisor,
        saves: SAVES.load(Ordering::Relaxed),
        save_errors: SAVE_ERRORS.load(Ordering::Relaxed),
        reloads: RELOADS.load(Ordering::Relaxed),
        warnings: WARNINGS
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone(),
    }
}
