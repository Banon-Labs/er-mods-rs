//! Arming the System>Quit rows from a host that has nothing else.
//!
//! [`crate::row_cloner::arm`] installs the cloner and the router, which is all a host needs when it
//! already owns the surrounding machinery -- the product does, and calls that directly. A
//! standalone shell owns none of it, and three further things have to exist or the rows are
//! decoration:
//!
//! 1. **The grid.** Vanilla `02_040_optionsetting` ships a Quit Game panel with two cells. The
//!    cloned rows are cells three through six, so without the derived six-cell movie the cloner
//!    appends rows into cells that do not exist.
//! 2. **The menu pump.** The link field builds and submits a native `CS::SoftwareKeyboardJob`, and
//!    its display objects are only valid while its own window runs. Both are `MenuWindowJob::Run`
//!    work.
//! 3. **The game task.** A row press latches a request; the import that satisfies it mutates the
//!    inventory and `PlayerGameData` and must run on `FrameBegin`.
//!
//! 4. **The browser's own activation.** While the live `05_010` window is this crate's directory
//!    browser, a row press is a browse step; forwarding it runs vanilla's OK handler and the game
//!    asks **Start with selected profile** over a folder.
//! 5. **The Save Game row's text hook and stage task.** The row's words go onto the native first
//!    row through `MsgRepository::GetAndFormat`, and the stage machine a press latches advances on
//!    `FrameBegin`.
//!
//! Each is installed by its own module and reported separately, because each fails differently and
//! a run has to be able to say which one was missing.
//!
//! # Only the machinery the row set actually uses
//!
//! Items 2 and 3 exist for the link field and the importer behind it, and both cost a real detour:
//! the pump claims `MenuWindowJob::Run`, and the task registers on `CSTaskImp`. A shell arming no
//! build row has no field to drive and nothing to import, so installing them would put two claims
//! on the process for work that can never be requested. They are therefore installed only when the
//! row set contains a build row, and reported as [`None`] -- not as a failure and not as a success
//! -- when it does not. The grid and the rows themselves are needed by every row set.

use core::sync::atomic::Ordering;

use crate::host::append_autoload_debug;
use crate::row_cloner::{ArmError, QuitRowActions, RowSet};

/// What a standalone arm managed to install. Every field false is a load that will show no rows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StandaloneArm {
    /// The six-cell Quit grid and the link field's movie are being served.
    pub gfx_served: bool,
    /// The row cloner and the row router are installed.
    pub rows_armed: bool,
    /// The link field has a menu pump. `None` when this row set has no field to drive.
    pub menu_pump: Option<bool>,
    /// The import and the export have a game task to finish on. `None` when this row set has
    /// nothing to import or export.
    pub game_task: Option<bool>,
    /// The profile-renderer table is guarded, so opening `05_010_ProfileSelect` cannot fault in the
    /// native refresh. `None` when no row in this set opens that window.
    pub profile_table_guard: Option<bool>,
    /// A row press on the destination browser browses instead of running the game's own character
    /// load confirm. `None` when this row set opens no browser.
    pub picker_activate: Option<bool>,
    /// The Save Game stage machine has a `FrameBegin` task to advance on. `None` when no Save Game
    /// flow was supplied.
    pub save_flow_task: Option<bool>,
}

impl StandaloneArm {
    /// True only when every part this row set needs is in place.
    ///
    /// A [`None`] is not a missing part: it is machinery the armed rows never reach, so it is
    /// neither installed nor required. Only `Some(false)` -- asked for and refused -- fails.
    pub fn is_complete(&self) -> bool {
        self.gfx_served
            && self.rows_armed
            && self.menu_pump != Some(false)
            && self.game_task != Some(false)
            && self.profile_table_guard != Some(false)
            && self.picker_activate != Some(false)
            && self.save_flow_task != Some(false)
    }
}

/// Arm `rows` with no product behind them, routing presses to `actions`.
///
/// Call from a bootstrap thread, not from `DllMain`: the game-task registration waits for the
/// game's task manager to exist, and waiting inside the loader lock deadlocks the process.
///
/// `actions` must carry an entry for every row in `rows` that this crate does not drive itself.
/// The build rows are driven from inside the crate and need none; the two character rows need the
/// opener a shell supplies. A `None` beside a row that is in the set is a row that appears and does
/// nothing, which is worse than absent -- so a shell that cannot supply a flow must leave that row
/// out of `rows` rather than out of `actions`.
///
/// # Safety
///
/// Bootstrap thread, once per process, before the Quit tab has built a dialog.
pub unsafe fn arm_standalone(rows: RowSet, actions: QuitRowActions) -> StandaloneArm {
    // Before the swap hook, because that hook can be reached by a Scaleform load the moment it is
    // installed and this is the answer it reads. The `05_010` edit rewrites the window's layout to
    // make room for fields something has to fill; a host that fills none of them must leave the
    // window as the game built it. See `profile_select_chrome_gate` for what the edit changes and
    // for the run this was measured on.
    // Read off the actions, not off `rows` alone. The Save Game row has two spellings and only one
    // of them is a cloned row: `save_game_as_start_flow` comes with `rows.save_game_as`, while
    // `save_game_start_flow` takes the native first row over and clones nothing. Asking `rows` on
    // its own answers `false` for the take-over shape, which is how `er-save-game-row` came to hand
    // roll its own arm rather than call this function -- and then opened an undressed browser for a
    // day because the latch below has exactly one caller and it is here.
    let save_game_owns_native_row = actions.save_game_start_flow.is_some();
    let save_game_armed = save_game_owns_native_row || actions.save_game_as_start_flow.is_some();
    let browse_rows_armed = rows.load_character_from_file || rows.save_game_as || save_game_armed;
    // A shell with no product behind it decodes the game's own save for itself, so its character
    // rows carry the merged header and the attribute line rather than the game's three bare fields.
    // Installed before the gate is asked, because filling the seam is what earns the edit. A host
    // that installed its own answer first keeps it -- the seam is first-caller-wins, and the
    // product's answer knows about staged and picked saves this one deliberately does not.
    if rows.load_character {
        let _ = crate::profile_row_chrome::install_row_populate_hooks(
            crate::profile_row_chrome::RowPopulateHooks {
                character_row_facts: Some(
                    crate::standalone_character_rows::standalone_character_row_facts,
                ),
                ..crate::profile_row_chrome::RowPopulateHooks::default()
            },
        );
    }
    let host_dresses_character_rows = crate::profile_row_chrome::row_populate_hooks()
        .character_row_facts
        .is_some();
    let profile_chrome = crate::profile_select_chrome_gate::profile_select_chrome_required(
        browse_rows_armed,
        host_dresses_character_rows,
    );
    crate::gfx_swap::set_profile_05_010_edit_armed(profile_chrome);
    if !profile_chrome {
        append_autoload_debug(format_args!(
            "system-quit-gfx: 05_010 stats-panel edit stays off (browse_rows_armed={browse_rows_armed} host_dresses_character_rows={host_dresses_character_rows}); ProfileSelect renders the game's own five-row presentation with its face boxes"
        ));
    }
    // Which movies this row set actually opens, rather than every movie this crate can derive.
    //
    // The grid is the half that has to be asked about. Vanilla's Quit tab has two cells and the
    // six-cell derivation is for cloned rows, so a host that clones none and instead replaces the
    // native first row gets a widened grid with four empty cells -- bd
    // `slim-quickload-still-widened-the-quit-grid-2026-09-12`. `GfxServeSet::ALL_PICKER_KEYED`,
    // which this used to serve unconditionally, says `quit_grid: true` for every caller.
    //
    // The other three follow the same rule and cost nothing to get right: the link field's movie
    // belongs to the build rows, the path editor to the browse picker, and the picker's own cache
    // key to whichever row puts `05_010_ProfileSelect` on screen. The game's own `profile_select`
    // key stays false here, because serving the derived layout there re-lays out the title's
    // **Load Game** as well, and no shell's feature is character select.
    let clones_a_row = rows.load_character
        || rows.load_character_from_file
        || rows.load_build_from_url
        || rows.generate_build_link
        || rows.save_game_as;
    let serve = crate::gfx_swap::GfxServeSet {
        quit_grid: clones_a_row,
        build_url_field: rows.load_build_from_url || rows.generate_build_link,
        path_editor_field: browse_rows_armed,
        profile_select: false,
        profile_select_picker_key: rows.load_character || browse_rows_armed,
    };
    // Save Game as a row of its own is the seventh item on the tab, and the six-cell grid has
    // nowhere to put it: measured 2026-09-20, a live run with `save-game` listed cloned the row
    // and reported `cols=2 rows=3 navigable_cells=6 item_count=7`, so it existed and never
    // reached the screen. Asking for the seventh cell before the hook is installed, because the
    // derivation is cached the first time the movie is loaded and the flag is read there.
    if rows.save_game_as {
        crate::gfx_swap::serve_seventh_quit_cell();
    }
    // First of the installs, because it is the only one with a deadline: the movie is served the
    // first time the Quit tab is opened, and a swap registered after that shows a vanilla two-cell
    // grid until the panel is rebuilt.
    let gfx_served = unsafe { crate::gfx_swap::install_gfx_swap_hook_for(serve) };
    // The picker's ProfileSelect is served under a key of its own, so the derived movie -- which
    // hides the face box and compacts five 156px rows into ten 52px ones -- dresses a browse list
    // and leaves the title's Load Game the way the game ships it. Without this rebind the serve
    // set above has nowhere to land and the picker falls back to the vanilla presentation.
    // Before anything that can open the picker: a save can fire while it is open, and the records
    // it borrowed must not be what that save writes.
    let _ = unsafe { crate::row_staging::install_save_serialize_row_guard() };
    let picker_key =
        unsafe { crate::profile_select_movie_key::install_picker_profile_select_key() };
    if !picker_key {
        append_autoload_debug(format_args!(
            "system-quit-gfx: the picker's ProfileSelect cache key did not install; its browse rows will render in the game's own vanilla character presentation"
        ));
    }
    let rows_armed = match unsafe { crate::row_cloner::arm(rows, actions) } {
        Ok(()) => true,
        Err(ArmError::AlreadyArmed) => {
            append_autoload_debug(format_args!(
                "system-quit-dup: already armed in this process; leaving the first arm's rows alone"
            ));
            false
        }
        Err(error) => {
            append_autoload_debug(format_args!(
                "system-quit-dup: could not arm the Quit rows: {error:?}"
            ));
            false
        }
    };
    // The link field and the importer are the only reasons the game task exists, so a row set
    // without a build row neither installs nor needs it.
    let build_rows = rows.load_build_from_url || rows.generate_build_link;
    let game_task = build_rows.then(crate::game_task::install_build_row_game_task);
    // Both character rows open `05_010_ProfileSelect`, and that window renders a character model
    // per slot. The native refresh that draws them walks the renderer table without a null check,
    // so a host arming either row has to own the guard or the first press is an access violation
    // rather than a row -- measured 2026-09-11, `0xc0000005` at `eldenring.exe+0x9ab874`.
    // The product installs the same body from its own private detour and must not call this.
    let character_rows = rows.load_character || rows.load_character_from_file;
    // The Save Game row opens the same `05_010` window, by a different route: its destination
    // browser is the load picker pointed at a folder to write into. So everything below that was
    // written for "a character row is armed" is really for "this host puts a ProfileSelect on
    // screen", and asking the narrower question left the Save Game row with an undressed picker
    // under an un-hidden pause menu on run br-20260912-201454-d12a.
    //
    // `save_game_armed`, not `row_cloner::save_game_flow_is_owned()`, which reads
    // `save_game_start_flow` alone and so answers `false` for the added-row shape -- the shape a
    // default `er-save-game-row` shipped and the one this function now has to serve.
    let opens_profile_select = character_rows || save_game_armed;
    let profile_table_guard = opens_profile_select
        .then(|| unsafe { crate::profile_table_guard::install_profile_table_guard() });
    // A row press on our own browser is a browse step -- enter a folder, switch drive, page, pick a
    // file -- never a character load. Without this the press reaches vanilla's own OK handler and
    // the game asks **Start with selected profile** over a folder, which is what run
    // br-20260912-203044-5fbd put on screen. The product has its own detour on that address and
    // chains with this one through the union; a shell has nothing else.
    let picker_activate =
        browse_rows_armed.then(crate::save_picker_menu::install_picker_profile_load_activate_hook);
    // One detour, two reasons to want it. The link field needs a menu pump to submit its keyboard
    // job; a character row needs the same post-run moment to hide the pause menu behind the picker
    // it just opened and to put it back when the picker closes. Neither is the product's hook --
    // this is the shell's own, chained onto the same address through the union.
    crate::menu_pump::set_character_rows_armed(character_rows);
    crate::menu_pump::set_save_game_row_armed(save_game_armed);
    let menu_pump = (build_rows || opens_profile_select)
        .then(|| unsafe { crate::menu_pump::install_quit_menu_window_run_hook() });
    // The Save Game row's own two installs. The text substitution puts `Save Game`, its line help
    // and its confirm wording onto the native first row, and gates itself on
    // `save_game_flow_is_owned` -- so installing it in the added-row shape costs a hook and changes
    // no text, which is what keeps the two spellings off the tab at once. The task is what advances
    // the stage machine a press latches; without it a press latches a request nothing moves.
    let save_flow_task = save_game_armed.then(|| {
        crate::save_game_row::install_system_quit_save_game_text_hook();
        crate::save_flow::install_save_flow_game_task()
    });
    // The row-populate detour is what dresses a browse row: it hides the `Level` caption and the
    // bottom `PlayTime` that would otherwise read "Level 0" and "0:00:00" about a character that
    // does not exist, repurposes the top-right `Location` for the file's last-saved time, and
    // writes the drive strip and the current-path bar. Without it the picker opens onto the game's
    // own character presentation.
    //
    // The product installs the same two detours from its own private copy, and the two must never
    // both run: `scripts/me3-dll-conflicts.toml` records the pair as duplicate owners and the
    // profile generator refuses to emit a profile carrying both, so one owner per process is a
    // property of the conflict table rather than an assumption made here.
    if opens_profile_select {
        crate::profile_row_chrome::install_profile_row_populate_hooks();
    }
    let arm = StandaloneArm {
        gfx_served,
        rows_armed,
        menu_pump,
        game_task,
        profile_table_guard,
        picker_activate,
        save_flow_task,
    };
    // `not-required` rather than `None`: the line is read by a person looking for what went wrong,
    // and a bare `None` beside the booleans reads as a failure that printed oddly.
    let describe = |part: Option<bool>| match part {
        Some(true) => "yes",
        Some(false) => "FAILED",
        None => "not-required",
    };
    append_autoload_debug(format_args!(
        "system-quit-dup: standalone arm complete={} gfx_served={gfx_served} rows_armed={rows_armed} menu_pump={} game_task={} profile_table_guard={} picker_activate={} save_flow_task={} serve={serve:?} rows={rows:?}",
        arm.is_complete(),
        describe(menu_pump),
        describe(game_task),
        describe(profile_table_guard),
        describe(picker_activate),
        describe(save_flow_task)
    ));
    arm
}

/// Emit the build rows' telemetry counters as one machine-readable line.
///
/// A standalone shell writes no `er-quickload-telemetry.json`, so until this existed the only
/// record a shell run left behind was prose -- readable by a person, not assertable by a watcher,
/// and the reason a row press could only ever be reported as "seen in the log" rather than
/// measured. Every value here is a count the DLL derived from the game's own memory: a placement
/// counted only when the root proxy accepted a transform, an open counted only when the field's
/// window ran, an opened link counted only when `ShellExecuteW` said so.
///
/// Called at each row outcome rather than at teardown, because a shell has no teardown hook and a
/// run that crashes still leaves the last outcome's line on disk.
pub fn append_build_row_oracle_line(reason: &str) {
    use er_telemetry_core::counters as c;
    let load = |counter: &'static core::sync::atomic::AtomicUsize| counter.load(Ordering::SeqCst);
    // Each name says what its counter counts, because the first live run made the cost of not
    // doing that concrete. `url_requests` was `..._REQUEST_COUNT`, which counts imports handed to
    // the importer, while `link_requests` was the generate row's press. A cancelled link field
    // therefore printed `url_requests=0 link_requests=1`, which reads as "the build-url row never
    // fired" -- and the row had fired: it opened the field, the player backed out, and no import
    // was ever requested. The press counter it should have been showing was in the same module
    // and simply absent from the line.
    append_autoload_debug(format_args!(
        "system-quit-rows: oracle at={reason} \
         url_row_presses={} url_imports_requested={} url_editor_opens={} \
         url_window_placed={} url_window_unplaced={} \
         url_accepted={} url_cancelled={} url_imported={} url_rejected={} url_failed={} \
         link_row_presses={} link_exports_requested={} link_encoded={} link_clipboard={} \
         link_opened={} link_failed={} link_url_len={}",
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_ACTION_COUNT),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_REQUEST_COUNT),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_EDITOR_OPEN_COUNT),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_WINDOW_PLACED),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_WINDOW_UNPLACED),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_ACCEPTED_COUNT),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_CANCELLED_COUNT),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_IMPORTED_COUNT),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_REJECTED_COUNT),
        load(&c::SYSTEM_QUIT_LOAD_BUILD_URL_FAILED_COUNT),
        load(&c::SYSTEM_QUIT_GENERATE_BUILD_LINK_ACTION_COUNT),
        load(&c::SYSTEM_QUIT_GENERATE_BUILD_LINK_REQUEST_COUNT),
        load(&c::SYSTEM_QUIT_GENERATE_BUILD_LINK_ENCODED_COUNT),
        load(&c::SYSTEM_QUIT_GENERATE_BUILD_LINK_CLIPBOARD_COUNT),
        load(&c::SYSTEM_QUIT_GENERATE_BUILD_LINK_OPENED_COUNT),
        load(&c::SYSTEM_QUIT_GENERATE_BUILD_LINK_FAILED_COUNT),
        load(&c::SYSTEM_QUIT_GENERATE_BUILD_LINK_LAST_URL_LEN),
    ));
}
