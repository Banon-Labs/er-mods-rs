//! Standalone ME3 shell for the cloned System>Quit rows, with no product DLL in the profile.
//!
//! Which rows it arms comes from `er-quit-menu.toml` beside the game executable, not from a cargo
//! feature and not from a constant in this file:
//!
//! ```toml
//! rows = ["load-character", "load-character-from-file", "load-build-from-url", "generate-build-link"]
//! save_game = "add-row"
//! ```
//!
//! Everything the rows are made of lives in `er-quit-menu-core`, which the product DLL also links
//! and arms. This shell supplies the openers that crate already owns and leaves the product-only
//! answers at `None`, which the router reads as a press it must not carry out.
//!
//! # Why one shell with a file, and not three cdylibs
//!
//! It was three: `er-quit-menu` armed `RowSet::ALL`, `er-quit-load-character` armed the character
//! pair, `er-save-game-row` armed the Save Game row. 783 lines between them over one 27,086-line
//! feature crate, differing only in which `RowSet` bits they passed and which `QuitMenuHost` fields
//! they filled -- and every pair among them was a declared conflict, because each cdylib links its
//! own copy of the core and two copies is two owners of one tab.
//!
//! Halves were measured on 2026-09-12: both `AddCancelButton` detours sat on the hook union and
//! both fired, each shell cloned its pair into the same dialog and then again on the other's pass,
//! and the result was a six-cell grid holding `item_count=10`, two row tables disagreeing about
//! which index is Return to Desktop, and every press resolving `row=AMBIGUOUS`. One arm in one
//! process is the only shape that works without shared cross-DLL state.
//!
//! A cargo feature would have merged the packages and kept the problem one layer down: the
//! installer would ship a build per row set. A file is the one form a player can change.
//! See `docs/plans/menus-and-saves-consolidation.md`, phase 2.
//!
//! # What this shell installs that the product already had
//!
//! `er_quit_menu_core::arm::arm_standalone` owns that list and reports each part separately,
//! because each fails differently: the derived grid and the movies the rows stand on, a
//! `MenuWindowJob::Run` detour, a `FrameBegin` task, the browser's own activation, and the Save
//! Game row's text hook and stage task.
//!
//! # Never in the same profile as a `quit-rows` product
//!
//! Any other host that arms rows is a second cloner, and both derive the same movie --
//! `er_gfx::options_02_040::quit6` fail-closes when its input is not vanilla, so a second deriver
//! handed already-derived bytes correctly refuses. `scripts/me3-dll-conflicts.toml` records those
//! pairs and the profile generator refuses to emit a profile carrying both. A default
//! `er-quickload` is not one of them: it arms no rows and derives no grid since `quit-rows` came
//! off its default features.
//!
//! # What stays refused here
//!
//! Every product-owned answer in the host seam stays at its neutral default -- including the
//! save-write bypass, so a product-less load can never push a write past `er-save-suppress`.

// A cdylib whose every consumer is `DllMain` and the hooks it installs, all of them
// `#[cfg(windows)]`. On a host build the shell is compiled with its only callers cfg'd
// out, so `dead_code`/`unused_imports` there report the cfg, not real debt. The shipping
// target (x86_64-pc-windows-msvc) carries the full deny with no allows.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

use std::path::{Path, PathBuf};

use er_quit_menu_core::row_config::{QuitRowsConfig, SaveGameShape};

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_MAIN_SUCCESS: i32 = 1;
const LOG_FILE_NAME: &str = "er-quit-menu.log";

/// The player's own file, beside the game executable. Named after this DLL, the way
/// `er-quickload.toml` is named after that one.
const CONFIG_FILE_NAME: &str = "er-quit-menu.toml";

#[cfg(windows)]
static START: std::sync::Once = std::sync::Once::new();

/// Where the standalone log and the config file live: next to the executable, falling back to the
/// CWD.
fn game_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Fresh per process: the first line of a run truncates the file (rotating the previous
/// run's aside as `.log.prev`), later lines append. No log in this repo accumulates across
/// runs -- mixing evidence from builds that no longer exist is how a count over one file
/// gets read as one run's behaviour.
/// A run directory moves this file out of the game directory; without the knob it stays there.
///
/// That is not a tidiness point. On run br-20260912-183117-e19a this shell took the process down
/// inside the System-window restore, and the only line naming the window it died on was in this
/// log -- in the game directory, while the run's own artifact directory held twenty-five files and
/// none of them said it. Same failure `er-invasion-warp` had on 2026-09-08, same fix.
/// `scripts/er_artifact_env.py` carries the matching entry.
const LOG_PATH_ENV: &str = "ER_QUICKLOAD_QUIT_MENU_LOG_PATH";

fn append_log(dir: &Path, args: std::fmt::Arguments<'_>) {
    let _ = dir;
    er_game_base::log::append_line(
        &er_game_base::log::redirected_artifact_path(LOG_PATH_ENV, LOG_FILE_NAME),
        format_args!("er-quit-menu: {args}"),
    );
}

/// The standalone host seam: this DLL has no product behind it, so every product-owned
/// answer stays at its neutral default -- including the save-write bypass, which must stay
/// refused so a product-less load can never push a write past `er-save-suppress`.
fn install_standalone_host() {
    let _ = er_quit_menu_core::install_host(er_quit_menu_core::QuitMenuHost {
        append_autoload_debug: standalone_log,
        append_crash_log: standalone_log,
        ..er_quit_menu_core::QuitMenuHost::defaults()
    });
}

fn standalone_log(args: std::fmt::Arguments<'_>) {
    append_log(&game_dir(), args);
}

/// Read the player's file, writing a commented default when there is none.
///
/// An unreadable file is not a reason to arm nothing: the defaults are the row set this DLL
/// shipped with before the file existed, so a permissions problem costs the player their settings
/// and not their rows. Every departure from what the file asked for is logged, because a row that
/// silently did not appear is indistinguishable from a row that is broken.
fn load_config(dir: &Path) -> QuitRowsConfig {
    let path = dir.join(CONFIG_FILE_NAME);
    let contents = match std::fs::read_to_string(&path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let boilerplate = er_quit_menu_core::row_config::boilerplate_config();
            match std::fs::write(&path, &boilerplate) {
                Ok(()) => append_log(
                    dir,
                    format_args!(
                        "config: auto-created '{}' with the defaults",
                        path.display()
                    ),
                ),
                Err(write_error) => append_log(
                    dir,
                    format_args!(
                        "config: '{}' is missing and could not be created ({write_error}); using the defaults",
                        path.display()
                    ),
                ),
            }
            boilerplate
        }
        Err(error) => {
            append_log(
                dir,
                format_args!(
                    "config: '{}' could not be read ({error}); using the defaults",
                    path.display()
                ),
            );
            return QuitRowsConfig::default();
        }
    };
    let config = er_quit_menu_core::row_config::parse(&contents);
    for complaint in &config.complaints {
        append_log(dir, format_args!("config: {complaint}"));
    }
    config
}

/// Open the browse picker for a **Load Character from File** press.
///
/// The row router answers in booleans; the picker answers in outcomes, and the two disagree about
/// one case on purpose. `Dismissed` means a picker ran and the user backed out of it -- the press
/// was carried out, so the row must not re-arm -- while the router only needs to know whether the
/// press was taken. Collapsing them here rather than widening the router keeps that distinction
/// where the picker made it.
///
/// # Safety
///
/// Menu-thread press context, with `action_obj` the row's live action object.
#[cfg(windows)]
unsafe fn open_save_picker_for_row(action_obj: usize) -> bool {
    unsafe { er_quit_menu_core::save_picker_menu::system_quit_open_save_picker_menu(action_obj) }
        .request_discharged()
}

/// What a press on each selected row reaches.
///
/// A flow is supplied only when its row is in the set. The pairing is the safety property the row
/// set already carries -- an unarmed row is never cloned, so it can never be pressed -- stated
/// again on this side so a flow pointer cannot outlive the row that justified it.
#[cfg(windows)]
fn row_actions(config: &QuitRowsConfig) -> er_quit_menu_core::row_cloner::QuitRowActions {
    let rows = config.rows;
    // Which of the two Save Game slots the flow goes in is the whole difference between the
    // shapes: `save_game_start_flow` takes the native first row over and is what the text
    // substitution gates on, `save_game_as_start_flow` belongs to the cloned row and leaves both
    // vanilla rows with their own labels and their own actions.
    let save_game_flow = rows.save_game.then_some(
        er_quit_menu_core::save_game_row::system_quit_save_game_start_flow
            as unsafe fn(usize) -> bool,
    );
    let takes_the_native_row = config.save_game == SaveGameShape::ReplaceNativeRow;
    er_quit_menu_core::row_cloner::QuitRowActions {
        open_profile_load_dialog: rows.load_character.then_some(
            er_quit_menu_core::profile_load_dialog::system_quit_open_profile_load_dialog
                as unsafe fn(usize) -> bool,
        ),
        open_save_picker_menu: rows
            .load_character_from_file
            .then_some(open_save_picker_for_row as unsafe fn(usize) -> bool),
        save_game_start_flow: takes_the_native_row.then_some(save_game_flow).flatten(),
        save_game_as_start_flow: (!takes_the_native_row).then_some(save_game_flow).flatten(),
        save_game_request_save_only: rows.save_game.then_some(
            er_quit_menu_core::save_game_row::system_quit_save_game_request_save_only
                as unsafe fn(),
        ),
        ..er_quit_menu_core::row_cloner::QuitRowActions::default()
    }
}

/// Arm the configured rows. Runs on its own thread because the game-task registration waits for
/// the game's task manager to exist, and waiting inside the loader lock deadlocks the process.
#[cfg(windows)]
fn arm_configured_rows() {
    let dir = game_dir();
    let config = load_config(&dir);
    let rows = config.rows.row_set(config.save_game);
    append_log(
        &dir,
        format_args!(
            "config: arming {:?} (save_game={}, stated_in_the_file={})",
            config.rows.rows(),
            config.save_game.config_name(),
            config.rows_were_stated
        ),
    );
    if config.rows.is_empty() {
        append_log(
            &dir,
            format_args!(
                "config: no rows are selected, so this DLL adds nothing to the Quit tab; list one in '{CONFIG_FILE_NAME}' to get it back"
            ),
        );
        return;
    }
    // Safety: a bootstrap thread, once per process (`START` gates the spawn), before the Quit tab
    // has built a dialog.
    let arm = unsafe { er_quit_menu_core::arm::arm_standalone(rows, row_actions(&config)) };
    if !arm.is_complete() {
        append_log(
            &dir,
            format_args!(
                "some of the rows' machinery did not install: {arm:?}; the rows may be absent or inert"
            ),
        );
    }
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. Do not call directly.
pub unsafe extern "system" fn DllMain(
    module: *mut core::ffi::c_void,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // First, before anything that can panic. A panic in a cdylib crosses an
        // `extern "system"` boundary and becomes an abort, which does not dispatch to a
        // vectored handler -- so `er_crash_logging` writes no record at all and the process
        // just vanishes. This hook is what turns that silence into a file:line. The hook is
        // per-DLL: every cdylib links its own `er-game-base`, so another shell installing it
        // does nothing here. Enforced by `scripts/check-panic-reporter-installed.py`.
        er_game_base::panic_report::report_panics_to("er-quit-menu", standalone_log);

        let module_base = module as usize;
        START.call_once(|| {
            // Before the thread, so no moved code can run against an un-installed seam.
            install_standalone_host();
            append_log(
                &game_dir(),
                format_args!(
                    "loaded module_base=0x{module_base:x}; reading '{CONFIG_FILE_NAME}' for the rows to arm"
                ),
            );
            std::thread::spawn(arm_configured_rows);
        });
    }
    DLL_MAIN_SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_standalone_host_installs_exactly_once() {
        assert!(install_host_once());
        assert!(!install_host_once());
    }

    fn install_host_once() -> bool {
        er_quit_menu_core::install_host(er_quit_menu_core::QuitMenuHost {
            append_autoload_debug: standalone_log,
            append_crash_log: standalone_log,
            ..er_quit_menu_core::QuitMenuHost::defaults()
        })
    }
}

// Windows-only because the row set and the action table these assert on live in
// `er_quit_menu_core::row_cloner`, which is itself `#[cfg(windows)]`. They run under wine from the
// `cargo xwin test --lib` list in `scripts/check-rust-build.sh`, which is where every other
// windows-only unit test in this workspace runs.
#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;
    use er_quit_menu_core::row_config::{RowSelection, parse};

    /// The row set and the action table have to agree row for row. An armed row with no action is
    /// a row that appears and does nothing when pressed; an action beside a row that was never
    /// armed is a pointer to a flow no press can reach.
    ///
    /// Asserted over every configuration the file can name rather than one of them, because the
    /// pairing is now decided at runtime and a single example would prove only that example.
    #[test]
    fn every_armed_row_has_a_flow_and_no_unarmed_row_carries_one() {
        for names in [
            "[]",
            "[\"load-character\"]",
            "[\"load-character-from-file\"]",
            "[\"load-build-from-url\", \"generate-build-link\"]",
            "[\"save-game\"]",
            "[\"load-character\", \"load-character-from-file\", \"load-build-from-url\", \"generate-build-link\", \"save-game\"]",
        ] {
            for shape in ["add-row", "replace-native-row"] {
                let config = parse(&format!("rows = {names}\nsave_game = \"{shape}\"\n"));
                let actions = row_actions(&config);
                let rows = config.rows;
                assert_eq!(
                    actions.open_profile_load_dialog.is_some(),
                    rows.load_character,
                    "Load Character, rows={names} save_game={shape}"
                );
                assert_eq!(
                    actions.open_save_picker_menu.is_some(),
                    rows.load_character_from_file,
                    "Load Character from File, rows={names} save_game={shape}"
                );
                assert_eq!(
                    actions.save_game_request_save_only.is_some(),
                    rows.save_game,
                    "Save Game request, rows={names} save_game={shape}"
                );
                // Exactly one of the two Save Game slots, and only when the row was asked for.
                assert_eq!(
                    actions.save_game_start_flow.is_some(),
                    rows.save_game && shape == "replace-native-row",
                    "Save Game take-over flow, rows={names} save_game={shape}"
                );
                assert_eq!(
                    actions.save_game_as_start_flow.is_some(),
                    rows.save_game && shape == "add-row",
                    "Save Game added-row flow, rows={names} save_game={shape}"
                );
                // The row-table reset and the drive-strip note belong to the product's own picker
                // state, which a shell does not have.
                assert!(actions.row_table_reset.is_none());
                assert!(actions.note_drive_strip_click_event.is_none());
            }
        }
    }

    /// The take-over shape clones no Save Game row, so the cloner is asked for two rows on the tab
    /// plus whatever else was selected -- not three. Getting this backwards puts two buttons
    /// reading `Save Game` on one tab.
    #[test]
    fn the_two_save_game_shapes_ask_the_cloner_for_different_row_sets() {
        let added = parse("rows = [\"save-game\"]\nsave_game = \"add-row\"\n");
        let taken = parse("rows = [\"save-game\"]\nsave_game = \"replace-native-row\"\n");
        assert!(added.rows.row_set(added.save_game).save_game_as);
        assert!(!taken.rows.row_set(taken.save_game).save_game_as);
    }

    /// With no file at all this DLL arms what it armed before the file existed, so a player who
    /// updates it and never opens the config sees the tab they already had.
    #[test]
    fn the_default_row_set_is_the_four_cloned_rows() {
        let config = QuitRowsConfig::default();
        let rows = config.rows.row_set(config.save_game);
        assert_eq!(config.rows, RowSelection::DEFAULT);
        assert!(rows.load_character);
        assert!(rows.load_character_from_file);
        assert!(rows.load_build_from_url);
        assert!(rows.generate_build_link);
        assert!(!rows.save_game_as);
    }
}
