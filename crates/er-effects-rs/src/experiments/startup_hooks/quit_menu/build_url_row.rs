//! The System>Quit **Load Build from URL** row.
//!
//! The importer itself lives in `er-build-import-runtime`, which the standalone
//! `er-build-import-dll` shell also drives. This module is only the row's two halves inside the
//! product DLL: the PRESS, which hands the runtime a URL, and the per-frame TICK, which is the game
//! thread the runtime needs in order to touch anything.
//!
//! # Why the press cannot just do the import
//!
//! The press arrives on whichever thread dispatched the menu activation, inside a native
//! `PropertyNewButtonController` action. The import mutates the inventory, the `CSGaitemImp`
//! singleton, `PlayerGameData` and the equipment slots, and it begins with a blocking HTTPS GET.
//! Neither belongs there. So the press only calls `request`, which spawns the fetch worker and
//! returns; the recurring `FrameBegin` task then applies the parsed build once the game can take it
//! (params streamed, character in the world). That split is the runtime's whole shape, and this row
//! reuses it rather than inventing a second one.
//!
//! # Where the URL comes from
//!
//! `build_url` in the game-directory `er-effects.toml` -- the same key the autoload/boot import
//! reads, which is exactly what makes this row "the same thing, whenever you like". There is no
//! in-game text entry yet; the native `CS::SoftwareKeyboard` surface that
//! `save_picker_path_editor` drives for save paths is the way to add one, and it is a second
//! subsystem on a delicate proven surface -- tracked as bd `er-effects-rs-2yj9` rather than
//! smuggled in here.

use super::*;

/// What a row press did. Every variant is reported to the debug log and counted, because "the row
/// did nothing" and "the row started an import" look identical on screen until the character
/// changes.
#[derive(Clone, Debug)]
pub(crate) enum BuildUrlPress {
    /// A fetch was started for this URL.
    Started(String),
    /// No `build_url` key in `er-effects.toml`.
    NotConfigured,
    /// The runtime refused: an import is already in flight, or the link carries no `?b=<id>`.
    Refused(String),
}

impl BuildUrlPress {
    fn label(&self) -> String {
        match self {
            BuildUrlPress::Started(url) => format!("STARTED url={url:?}"),
            BuildUrlPress::NotConfigured => format!(
                "NOT-CONFIGURED (no `{}` in {})",
                er_build_import_runtime::BUILD_URL_KEY,
                er_build_import_runtime::CONFIG_FILE_NAME
            ),
            BuildUrlPress::Refused(reason) => format!("REFUSED ({reason})"),
        }
    }
}

/// Handle a confirmed press of the Load Build from URL row.
///
/// Thread-agnostic on purpose: it reads a config file and spawns a worker, and touches no game
/// state, so it is safe from a menu action thunk, a controller activation, or anywhere else the
/// row's confirm is observed.
pub(crate) fn system_quit_start_build_import() -> BuildUrlPress {
    SYSTEM_QUIT_LOAD_BUILD_URL_ACTION_COUNT.fetch_add(1, Ordering::SeqCst);
    // Drain first, because `request` clears the runtime's error slot as it claims the machine. The
    // per-frame drain in `system_quit_build_import_tick` has almost certainly already taken it, but
    // "almost certainly" is how a failure the player asked about goes missing -- and draining here
    // also guarantees the line lands ABOVE this press in the log, attributed to the right request.
    drain_build_import_failure();
    let Some(url) = er_build_import_runtime::configured_build_url() else {
        SYSTEM_QUIT_LOAD_BUILD_URL_REFUSED_COUNT.fetch_add(1, Ordering::SeqCst);
        return BuildUrlPress::NotConfigured;
    };
    match er_build_import_runtime::request(&url) {
        Ok(()) => {
            SYSTEM_QUIT_LOAD_BUILD_URL_REQUEST_COUNT.fetch_add(1, Ordering::SeqCst);
            BuildUrlPress::Started(url)
        }
        Err(err) => {
            SYSTEM_QUIT_LOAD_BUILD_URL_REFUSED_COUNT.fetch_add(1, Ordering::SeqCst);
            BuildUrlPress::Refused(err.to_string())
        }
    }
}

/// Log and return a press outcome in one step, so both routing hooks report it identically.
pub(crate) fn system_quit_log_build_import_press(site: &str, press: &BuildUrlPress) {
    append_autoload_debug(format_args!(
        "system-quit-build-url: row press at {site} -> {}; the import itself runs on the FrameBegin task once the character can take it",
        press.label()
    ));
}

/// Report the runtime's last ASYNCHRONOUS failure, once, if there is one pending.
///
/// A press returns `Ok` the instant the worker is spawned, so a 404, an unparseable payload or a
/// build whose level and attributes disagree all fail long afterwards -- and land only in
/// `er-build-import.log`. A reader following the row press through
/// `er-effects-autoload-debug.log` would otherwise see "STARTED" and never learn it went nowhere.
fn drain_build_import_failure() {
    let Some(reason) = er_build_import_runtime::take_error() else {
        return;
    };
    SYSTEM_QUIT_LOAD_BUILD_URL_FAILED_COUNT.fetch_add(1, Ordering::SeqCst);
    append_autoload_debug(format_args!(
        "system-quit-build-url: the queued import FAILED and applied nothing -- {reason}"
    ));
}

/// One frame of the build importer, driven from the product's recurring `FrameBegin` task.
///
/// Does nothing at all until a press has queued a build, which is why it is safe to call every
/// frame from boot: `er_build_import_runtime::tick` returns immediately unless its phase is `Ready`.
///
/// It also drains the runtime's async failures -- see [`drain_build_import_failure`].
///
/// # Safety
///
/// Game task thread only -- the context every mutation inside the runtime requires.
pub(crate) unsafe fn system_quit_build_import_tick() {
    drain_build_import_failure();
    // Safety: the caller's contract (FrameBegin game task) carries through.
    let Some(report) = (unsafe { er_build_import_runtime::tick() }) else {
        return;
    };
    SYSTEM_QUIT_LOAD_BUILD_URL_IMPORTED_COUNT.fetch_add(1, Ordering::SeqCst);
    append_autoload_debug(format_args!(
        "system-quit-build-url: import applied to the live character -- build={:?} {}; every count here is a READ-BACK from game memory, not a call that returned",
        report.build_name,
        report.summary()
    ));
}
