//! Standalone ME3 shell for the **Save Game** row, and nothing else.
//!
//! Vanilla's first System>Quit row saves and returns you to the title screen. This DLL renames it
//! to `Save Game`, replaces its line help and its confirm text, and routes the press into the
//! destination browser `er-quit-menu-core` draws for **Load Character from File** -- so the player
//! chooses where the save goes and then keeps playing.
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
//! No cloned character rows (`RowSet::NONE` clones nothing), no autoload, no loading cover, no
//! portraits. Every product-owned answer in the host seam stays at its neutral default, including
//! the save-write bypass -- so a profile carrying only this row writes what the player asked for
//! and nothing else.

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

/// Arm the vanilla Save Game row: no clones, one flow.
///
/// Runs on its own thread because the game-task registration waits for the game's task manager to
/// exist, and waiting inside the loader lock deadlocks the process.
#[cfg(windows)]
fn arm_save_game_row() {
    // Safety: a bootstrap thread, once per process (`START` gates the spawn), before the Quit tab
    // has built a dialog.
    let armed = unsafe {
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
    };
    match armed {
        Ok(()) => standalone_log(format_args!(
            "armed the vanilla Save Game row: no cloned rows, the destination browser behind the press"
        )),
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
    // Only that movie. The six-cell Quit grid is for cells three through six, and this row replaces
    // a vanilla row that already exists -- serving the grid here would widen a two-cell tab and
    // leave four empty cells behind.
    // Safety: the bootstrap thread, before the title has loaded its movies; the installer is latched.
    if !unsafe {
        er_quit_menu_core::gfx_swap::install_gfx_swap_hook_for(
            er_quit_menu_core::gfx_swap::GfxServeSet::PROFILE_SELECT_ONLY,
        )
    } {
        standalone_log(format_args!(
            "the 05_010 movie is not being served; the destination browser will render in the game's own vanilla character presentation"
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
        START.call_once(|| {
            install_standalone_host();
            standalone_log(format_args!("attached"));
            std::thread::spawn(arm_save_game_row);
        });
    }
    DLL_MAIN_SUCCESS
}
