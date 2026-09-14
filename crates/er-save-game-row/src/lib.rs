//! Standalone ME3 shell for the **Save Game** row, and nothing else.
//!
//! Vanilla's first System>Quit row saves and returns you to the title screen. This DLL puts a row
//! reading `Save Game` on the tab that instead opens the destination browser `er-quit-menu-core`
//! draws for **Load Character from File** -- so the player chooses where the save goes and then
//! keeps playing.
//!
//! # Two shapes, one of them compiled
//!
//! By default the row is **added**: a third row is cloned onto the Quit tab and the two rows
//! FromSoft ships are left exactly as they are, label and action both. The `hijack-quit-row`
//! feature selects the other shape, which is what this crate did before: the native first row is
//! relabelled `Save Game` through `MsgRepository::GetAndFormat` and its action is replaced, so the
//! tab stays at two rows. Only the arm call differs -- the flow, the browser chrome and every other
//! install below are the same in both -- and the first lines of the log name which one armed.
//!
//! The relabelling is not a second switch to keep in step: the substitution asks
//! `er_quit_menu_core::row_cloner::save_game_flow_is_owned` first, which is true only while
//! `save_game_start_flow` is supplied. The default shape supplies `save_game_as_start_flow`
//! instead, so the native row keeps its own words and the two `Save Game` spellings can never be on
//! screen at once.
//!
//! # Why the row needed a crate before it could have a shell
//!
//! The row is three things and they used to live in three places: the label came from a
//! `MsgRepository::GetAndFormat` detour inside `er-quickload`, the press router from
//! `er-quit-menu-core`, and the flow behind the press from `er-quickload`'s own module tree. Only a
//! build with all three behaved; a build with the first two renamed a button and then ran vanilla's
//! action, which is what run br-20260912-185308-639d did -- a button reading `Save Game` that
//! quit to the title. The flow moved beside the chrome it drives on 2026-09-12, and this shell is
//! what that move was for.
//!
//! # What this shell does not carry
//!
//! No character rows and no build rows -- the only row it can clone is its own -- no autoload, no
//! loading cover, no portraits. Every product-owned answer in the host seam stays at its neutral
//! default, including the save-write bypass -- so a profile carrying only this row writes what the
//! player asked for and nothing else.

// A cdylib whose every consumer is `DllMain` and the hooks it installs, all of them
// `#[cfg(windows)]`. On a host build the shell is compiled with its only callers cfg'd out.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

use std::path::{Path, PathBuf};

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_MAIN_SUCCESS: i32 = 1;
const LOG_FILE_NAME: &str = "er-save-game-row.log";

#[cfg(windows)]
static START: std::sync::Once = std::sync::Once::new();

/// Where the standalone log lands when nothing redirects it: next to the executable.
fn log_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// A run directory moves this file out of the game directory; `scripts/er_artifact_env.py` carries
/// the matching entry. A game-directory log is single-slot, so two launches lose the run before
/// last -- and the line naming why a row did nothing is exactly the line that gets lost.
const LOG_PATH_ENV: &str = "ER_QUICKLOAD_SAVE_GAME_ROW_LOG_PATH";

/// Fresh per process: the first line truncates the file, rotating the previous run's aside.
fn append_log(dir: &Path, args: std::fmt::Arguments<'_>) {
    let _ = dir;
    er_game_base::log::append_line(
        &er_game_base::log::redirected_artifact_path(LOG_PATH_ENV, LOG_FILE_NAME),
        format_args!("er-save-game-row: {args}"),
    );
}

fn standalone_log(args: std::fmt::Arguments<'_>) {
    append_log(&log_dir(), args);
}

/// The standalone host seam: the two log sinks, and nothing else.
///
/// Everything else the browser needs now has a working default rather than a refusal, which is what
/// the last four runs were spent finding. `save_dest_start_dir` resolves the live save from
/// `%APPDATA%/EldenRing/<steamid>`, `save_picker_stage_row_records` and `save_dest_set_target` route
/// back into this crate's own functions instead of out through a host that would call them here
/// anyway, and `install_msgbox_builder_capture` registers the save-flow half of the
/// `CS::MessageBoxDialog` builder on the union -- so `builder_capture_live` is true and picking an
/// existing destination raises the overwrite confirm instead of being refused.
fn install_standalone_host() {
    let _ = er_quit_menu_core::install_host(er_quit_menu_core::QuitMenuHost {
        append_autoload_debug: standalone_log,
        append_crash_log: standalone_log,
        ..er_quit_menu_core::QuitMenuHost::defaults()
    });
}

/// Add the row: one clone, both vanilla rows untouched.
///
/// # Safety
///
/// The bootstrap thread, once per process, before the Quit tab has built a dialog.
#[cfg(all(windows, not(feature = "hijack-quit-row")))]
unsafe fn arm_rows() -> Result<(), er_quit_menu_core::row_cloner::ArmError> {
    unsafe {
        er_quit_menu_core::row_cloner::arm(
            er_quit_menu_core::row_cloner::RowSet {
                save_game_as: true,
                ..er_quit_menu_core::row_cloner::RowSet::NONE
            },
            er_quit_menu_core::row_cloner::QuitRowActions {
                // The cloned row's slot, which is also what keeps the native first row's label and
                // action vanilla: the text substitution is gated on the other slot being supplied.
                save_game_as_start_flow: Some(
                    er_quit_menu_core::save_game_row::system_quit_save_game_start_flow,
                ),
                save_game_request_save_only: Some(
                    er_quit_menu_core::save_game_row::system_quit_save_game_request_save_only,
                ),
                ..er_quit_menu_core::row_cloner::QuitRowActions::default()
            },
        )
    }
}

/// Take the vanilla first row over: no clones, one flow. What this crate did before the row moved
/// onto one of its own.
///
/// # Safety
///
/// The bootstrap thread, once per process, before the Quit tab has built a dialog.
#[cfg(all(windows, feature = "hijack-quit-row"))]
unsafe fn arm_rows() -> Result<(), er_quit_menu_core::row_cloner::ArmError> {
    unsafe {
        er_quit_menu_core::row_cloner::arm(
            er_quit_menu_core::row_cloner::RowSet::NONE,
            er_quit_menu_core::row_cloner::QuitRowActions {
                save_game_start_flow: Some(
                    er_quit_menu_core::save_game_row::system_quit_save_game_start_flow,
                ),
                save_game_request_save_only: Some(
                    er_quit_menu_core::save_game_row::system_quit_save_game_request_save_only,
                ),
                ..er_quit_menu_core::row_cloner::QuitRowActions::default()
            },
        )
    }
}

/// The Scaleform movies this shape needs.
///
/// The grid is the half that differs. Vanilla's Quit tab has two cells, and the derivation widens
/// it to six; a cloned row lands in the third, so the added-row shape cannot be reached without it,
/// while the take-over shape replaces a row that already exists and serving the grid there would
/// widen a two-cell tab and leave four empty cells behind (bd
/// `slim-quickload-still-widened-the-quit-grid-2026-09-12`). The three cells this shape leaves
/// empty are not clickable either: the native hit test discards any cell whose item index is past
/// the list's item count.
#[cfg(all(windows, not(feature = "hijack-quit-row")))]
const MOVIES: er_quit_menu_core::gfx_swap::GfxServeSet = er_quit_menu_core::gfx_swap::GfxServeSet {
    quit_grid: true,
    ..er_quit_menu_core::gfx_swap::GfxServeSet::PICKER_KEYED
};
#[cfg(all(windows, feature = "hijack-quit-row"))]
const MOVIES: er_quit_menu_core::gfx_swap::GfxServeSet =
    er_quit_menu_core::gfx_swap::GfxServeSet::PICKER_KEYED;

/// What the armed shape is, in the log's own words. The first lines of a run have to say whether
/// the tab has two rows or three, because every later line reads the same either way.
#[cfg(all(windows, not(feature = "hijack-quit-row")))]
const ARMED_SHAPE: &str = "armed the cloned Save Game row: three rows on the Quit tab, the two vanilla rows left with their own labels and their own actions, the destination browser behind the added row";
#[cfg(all(windows, feature = "hijack-quit-row"))]
const ARMED_SHAPE: &str =
    "armed the vanilla Save Game row: no cloned rows, the destination browser behind the press";

/// Arm the Save Game row, in whichever shape this build selected, and install everything the press
/// behind it needs.
///
/// Runs on its own thread because the game-task registration waits for the game's task manager to
/// exist, and waiting inside the loader lock deadlocks the process.
#[cfg(windows)]
fn arm_save_game_row() {
    // Safety: a bootstrap thread, once per process (`START` gates the spawn), before the Quit tab
    // has built a dialog.
    let armed = unsafe { arm_rows() };
    match armed {
        Ok(()) => standalone_log(format_args!("{ARMED_SHAPE}")),
        Err(error) => standalone_log(format_args!(
            "arming the Save Game row failed: {error:?} -- the row keeps the game's own text and action"
        )),
    }
    er_quit_menu_core::save_game_row::install_system_quit_save_game_text_hook();
    // The destination browser is `05_010_ProfileSelect` pointed at a folder to write into, so this
    // row needs the two installs that were written for the character rows and gated on them.
    //
    // The row-populate detour is the browser's chrome: it hides the `Level` caption and the bottom
    // `PlayTime` that would otherwise describe a character no browse row has, repurposes the
    // top-right `Location` for the file's last-saved time, and writes the drive strip and the
    // current-path bar. Without it the picker opens onto the game's own character presentation,
    // which is what run br-20260912-201454-d12a put on screen.
    er_quit_menu_core::profile_row_chrome::install_profile_row_populate_hooks();
    // A row press on our browser is a browse step -- enter a folder, switch drive, page, pick a
    // file -- never a character load. Without this the press reaches vanilla's own OK handler and
    // the game asks "Start with selected profile" over a folder, which is what run
    // br-20260912-203044-5fbd put on screen.
    if !er_quit_menu_core::save_picker_menu::install_picker_profile_load_activate_hook() {
        standalone_log(format_args!(
            "the destination browser's rows are not intercepted; pressing one will run the game's own character-load confirm"
        ));
    }
    // ...and the movie that detour dresses. It recognises one of our rows by an `ErCharStats` child
    // that exists only in the derived `05_010` movie, so hooking the populate without serving the
    // movie dresses nothing: run br-20260912-201935-ad27 scored every row foreign 13 times over and
    // the picker rendered in the game's own vanilla presentation.
    //
    // Which movies go with it depends on the shape: see `MOVIES`.
    // Safety: the bootstrap thread, before the title has loaded its movies; the installer is latched.
    if !unsafe { er_quit_menu_core::gfx_swap::install_gfx_swap_hook_for(MOVIES) } {
        standalone_log(format_args!(
            "the 05_010 movie is not being served; the destination browser will render in the game's own vanilla character presentation"
        ));
    }
    // ...and the rebind that keeps that derivation off the title's Load Game. The derived movie is
    // a re-layout of character select -- face box hidden, ten 52px rows where vanilla has five --
    // which is right for browsing destinations and wrong for choosing a character. The picker gets
    // its own Scaleform cache key; every other opener keeps the game's own.
    // Safety: the bootstrap thread, once per process, and the installer is itself latched.
    // A save can fire while the picker is open -- one did, on run br-20260913-021311-a481, and it
    // wrote the browse labels into the player's container. Every save goes through the serializer
    // this guards, so it is the one restore that cannot be routed around.
    // Safety: the bootstrap thread, once per process, and the installer is itself latched.
    let _ = unsafe { er_quit_menu_core::row_staging::install_save_serialize_row_guard() };
    if !unsafe { er_quit_menu_core::profile_select_movie_key::install_picker_profile_select_key() }
    {
        standalone_log(format_args!(
            "the picker's ProfileSelect cache key did not install; the destination browser will share the title Load Game screen's movie and re-lay it out"
        ));
    }
    // And the renderer-table guard, for the same reason a character row installs it: the native
    // refresh that draws a row's character model walks the profile model renderer table with no
    // null check. Installing it here repairs the table at the refresh rather than at the call site.
    // Safety: the bootstrap thread, once per process, and the installer is itself latched.
    if !unsafe { er_quit_menu_core::profile_table_guard::install_profile_table_guard() } {
        standalone_log(format_args!(
            "the profile renderer-table guard did not install; the destination browser's rows may draw without their character models"
        ));
    }
    // The two drivers the press needs and this shell is the only owner of: the browser opens from
    // inside `MenuWindowJob::Run`, and the stage machine behind it advances on a `FrameBegin` task.
    er_quit_menu_core::menu_pump::set_save_game_row_armed(true);
    if !er_quit_menu_core::save_flow::install_save_flow_game_task() {
        standalone_log(format_args!(
            "the Save Game stage machine has no task; a press would latch a request nothing advances"
        ));
    }
    // Safety: the bootstrap thread, once per process, and the installer is itself latched.
    unsafe { er_quit_menu_core::menu_pump::install_quit_menu_window_run_hook() };
}

/// # Safety
///
/// Called by the loader with the module handle and reason; the body spawns a thread rather than
/// doing work under the loader lock.
#[cfg(windows)]
#[unsafe(no_mangle)]
pub unsafe extern "system" fn DllMain(_module: usize, reason: u32, _reserved: usize) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // First, before anything that can panic. A panic in a cdylib crosses an
        // `extern "system"` boundary and becomes an abort, and an abort does not dispatch to a
        // vectored handler -- so `er_crash_logging` writes no record and the process just
        // vanishes. This hook is what turns that silence into a file:line. It is per-DLL: every
        // cdylib links its own `er-game-base`, so another shell installing it does nothing here.
        // Enforced by `scripts/check-panic-reporter-installed.py`.
        er_game_base::panic_report::report_panics_to("er-save-game-row", standalone_log);
        START.call_once(|| {
            install_standalone_host();
            standalone_log(format_args!("attached"));
            std::thread::spawn(arm_save_game_row);
        });
    }
    DLL_MAIN_SUCCESS
}
