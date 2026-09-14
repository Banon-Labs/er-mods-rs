//! The dependency-injection seam between this feature crate and its host DLL.
//!
//! Same pattern as `er_loading_portrait_core::host`: function pointers installed once at DLL
//! attach, neutral defaults until then, crate-internal wrappers bearing the exact names
//! the moved code already calls.
//!
//! This seam is larger than `er-save-picker-core`'s because the quit menu genuinely shares
//! state with the rest of the product: the ProfileSummary save-swap ledger is read by the
//! loading-cover slot resolution, the portrait slot is read by the loading-screen
//! pipeline, and the save-suppression bypass belongs to `er-save-suppress`. Every field
//! below is one measured cross-call with a consumer outside the quit-menu feature -- a
//! cross-call whose only consumers are inside the feature is a move, not a seam entry, per
//! the 2026-07-30 rule that no extracted crate reaches back into `er-quickload`.
//!
//! Fields land per slice: this scaffold carries the entries that cross with std/primitive
//! types. Entries needing a reshaped type (the save-swap ledger, the serialized-slot
//! reader) are specified in docs/plans/save-picker-crate-extraction.md and land with the
//! slice that moves their caller.

use std::path::PathBuf;
use std::sync::OnceLock;

/// Where a save-destination browser opens, and what loaded file it is saving beside.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SaveDestOrigin {
    pub start_dir: PathBuf,
    pub loaded_file_name: String,
    pub loaded_path: PathBuf,
}

/// Host callbacks the quit menu reads through the seam. Every field has a neutral default
/// (see [`QuitMenuHost::defaults`]); hosts overwrite the ones they own.
#[derive(Clone, Copy)]
pub struct QuitMenuHost {
    /// The directory the game's own save writer opens files in, when a host redirects it.
    ///
    /// The commit window builds its accepted-path set from this, so a write that lands anywhere
    /// else is refused rather than followed. A host with no redirect answers `None` and the set is
    /// built from the live path alone -- correct, because without a redirect there is nowhere else
    /// for the write to go.
    pub save_redirect_native_source_dir: fn() -> Option<std::path::PathBuf>,
    /// Install the `CS::MessageBoxDialog` builder capture, which is what makes the Save Game
    /// overwrite confirm answerable: the detour records the dialog pointer the flow's stage machine
    /// polls. The product installs it at boot for its own reasons and the call is idempotent, so the
    /// row press asks for it again rather than depending on that ordering. A host without one leaves
    /// the default: the destination list still opens, and a pick that would clobber an existing file
    /// is refused instead of written blind.
    pub install_msgbox_builder_capture: fn(),
    // --- logging ----------------------------------------------------------------------
    /// Structured debug logging sink (the product's `append_autoload_debug`).
    pub append_autoload_debug: fn(std::fmt::Arguments<'_>),
    /// Crash-log sink for the paths that must survive a hard fault.
    pub append_crash_log: fn(std::fmt::Arguments<'_>),

    // --- save source / redirect (owner: experiments::save_redirect, stays in product) --
    /// `%APPDATA%\EldenRing\<steamid>` when it is readable; `None` otherwise.
    pub default_save_root: fn() -> Option<std::path::PathBuf>,
    /// True when the active runtime is Seamless Co-op, so browsers offer `.co2` too.
    pub save_picker_seamless_mode_after_settle: fn(&str) -> bool,
    /// The loaded save's full path, or a reason string naming why it is unresolvable.
    pub system_quit_env_save_path: fn() -> Result<String, &'static str>,
    /// The loaded save's directory, or a reason string.
    pub system_quit_env_save_dir: fn() -> Result<String, &'static str>,
    /// Rewrite a foreign container's embedded Steam ID to the active user's, in place.
    pub normalize_save_bytes_to_active_steam_id: fn(&mut [u8]) -> bool,

    // --- loading-cover / autoload state (owner: product, genuinely shared) -------------
    /// The live `CS::ProfileSummary` pointer, or 0.
    pub system_quit_profile_summary_ptr: unsafe fn() -> usize,
    /// The loaded character's save slot (collapsed form; 0 when unknown).
    pub portrait_loaded_slot: fn() -> i32,
    /// The loaded slot only when a real source names it; `None` otherwise.
    pub portrait_loaded_slot_confirmed: fn() -> Option<i32>,
    /// The slot the loading-screen pipeline should target (selection-aware).
    pub portrait_target_slot: fn() -> i32,
    /// Rebuild the profile render table for a loading screen, if the state calls for it.
    pub maybe_build_profile_table_for_loading: unsafe fn(usize) -> bool,
    /// Drive one profile-renderer tick for `slot`. Product-owned: the loading-screen
    /// profile-model render drive is called from the task registration and the title tick,
    /// so it stays behind even though the quit menu also calls it.
    pub force_profile_render_tick: unsafe fn(usize, i32),
    /// True while a native loading screen is up.
    pub native_loading_screen_active: unsafe fn(usize) -> bool,

    // --- input ownership (owner: experiments::input_block, stays in product) -----------
    /// The game's main top-level window (dialog owner, dim geometry source). 0 = none.
    pub game_main_window: fn() -> usize,
    /// Release the input block immediately, before an irreversible quit.
    pub release_input_block_now: fn(),

    // --- save suppression (owner: er-save-suppress, already its own crate) ------------
    /// Take the one-shot bypass token that makes the Save Game row the only path whose
    /// save enqueue is really forwarded. False when no token was available.
    pub take_save_write_bypass: fn(&'static str) -> bool,

    // --- product gates ----------------------------------------------------------------
    /// True on a product autoload run (as opposed to a bare diagnostic load).
    pub product_autoload_enabled: fn() -> bool,
    /// True while a switch-driven reload owns the flow.
    pub switch_reload_active: fn() -> bool,
    /// True when the configured picker surface is the OS-native common-file dialog.
    /// The dim overlay is a quit-menu concern, but the surface decision is product-wide.
    pub os_native_picker_active: fn() -> bool,
    /// Redact a Windows-form path for debug logs.
    pub windows_path_for_log: fn(&str) -> String,

    // --- OS-dialog System>Quit picker entrypoints -------------------------------------
    /// Recover the owning System dialog from a row action object.
    pub system_dialog_from_action_obj: unsafe fn(usize) -> usize,
    /// Restore the live ProfileSummary after a non-staging picker return.
    pub system_quit_save_swap_restore_profile_summary: unsafe fn(&str),
    /// Arm the original loaded save's ProfileSummary before a source browse.
    pub system_quit_save_swap_arm_original: fn(&str) -> bool,
    /// Start directory for the source browse surface.
    pub save_picker_start_dir: fn() -> Option<PathBuf>,
    /// Ingest a picked foreign save through the root product pipeline.
    pub system_quit_ingest_picked_save: unsafe fn(&str) -> bool,
    /// Start directory and default filename for the destination browser.
    pub save_dest_start_dir: fn() -> Option<SaveDestOrigin>,
    /// Stage the chosen save destination target in the product save-flow state machine.
    pub save_dest_set_target: fn(PathBuf, &'static str),

    // --- the save picker's own browse surface (owner: product, until that surface moves) ------
    /// Re-stage the picker's browse rows onto `model`, returning whether any row was written.
    /// Reached from the shared software keyboard when a path editor accepts, which is the one
    /// caller of the picker's surface that now lives on this side of the seam.
    pub save_picker_stage_row_records: unsafe fn(&er_save_picker_core::SavePickerModel) -> bool,
    /// Re-arm the path editor's end-caret for a newly opened picker field. The link field has its
    /// own latch in `scaleform_proxy`; this one belongs to the picker.
    pub reset_path_editor_caret_latch: fn(),

    // --- the build importer's after-effects (owner: the loading-cover pipeline) ---------------
    /// An import has just landed on the live character: re-derive its record and rebuild both
    /// character portraits. Product-owned because the per-slot data-change replica belongs beside
    /// the loading-cover pipeline that also drives it; a shell has no such pipeline, so its
    /// neutral default is to do nothing and the import still applies.
    pub build_import_applied: unsafe fn(),
}

fn default_log(_args: std::fmt::Arguments<'_>) {}
fn default_gate_off() -> bool {
    false
}
/// `%APPDATA%/EldenRing`, read from the environment.
///
/// This is the game's own save folder, not a product setting, so it is a default rather than a
/// refusal: a shell with no save-redirect behind it still needs somewhere for a file browser to
/// open, and every ELDEN RING install on this machine writes here. A host with a redirect
/// overrides it with the root that redirect is actually using.
fn default_no_root() -> Option<std::path::PathBuf> {
    std::env::var_os("APPDATA")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("USERPROFILE").map(|profile| {
                std::path::PathBuf::from(profile)
                    .join("AppData")
                    .join("Roaming")
            })
        })
        .map(|appdata| appdata.join("EldenRing"))
}
fn default_seamless(_reason: &str) -> bool {
    false
}
/// The game's own active save container, found by looking where the game puts it.
///
/// A host that owns a save redirect answers this from the redirect's own state and this default
/// never runs. A shell has no redirect, and refusing here would be a refusal to *read* -- it would
/// stop a file browser opening at all, which is what left the standalone **Load Character from
/// File** row inert on 2026-09-11 (`save-picker: refused to open -- no host installed`).
///
/// So it looks: `%APPDATA%/EldenRing/<steamid>/ER0000.{sl2,co2}`, where `<steamid>` is the numeric
/// account directory the game creates. The newest container wins when an account has both, which
/// is the one the running session is using. Nothing is written here and nothing is guessed -- an
/// account directory that holds no container is skipped, and no directory at all is still an
/// error.
fn default_no_save_path() -> Result<String, &'static str> {
    let root = default_no_root().ok_or("no APPDATA or USERPROFILE in the environment")?;
    let entries = std::fs::read_dir(&root).map_err(|_| "no %APPDATA%/EldenRing directory")?;
    let mut newest: Option<(std::time::SystemTime, std::path::PathBuf)> = None;
    for entry in entries.flatten() {
        if !entry
            .file_name()
            .to_string_lossy()
            .chars()
            .all(|c| c.is_ascii_digit())
        {
            continue;
        }
        for container in ["ER0000.sl2", "ER0000.co2"] {
            let candidate = entry.path().join(container);
            let Ok(meta) = candidate.metadata() else {
                continue;
            };
            let Ok(modified) = meta.modified() else {
                continue;
            };
            if newest.as_ref().is_none_or(|(seen, _)| modified > *seen) {
                newest = Some((modified, candidate));
            }
        }
    }
    let (_, path) = newest.ok_or("no ER0000 container under %APPDATA%/EldenRing")?;
    Ok(path.to_string_lossy().into_owned())
}

/// The directory half of [`default_no_save_path`].
fn default_no_save_dir() -> Result<String, &'static str> {
    let path = default_no_save_path()?;
    let separator = path
        .rfind(['/', '\\'])
        .ok_or("the resolved save has no parent directory")?;
    Ok(path[..separator].to_owned())
}
fn default_normalize(_bytes: &mut [u8]) -> bool {
    false
}
/// The live `CS::ProfileSummary`, read the way `er-profile-summary-core` reads it.
///
/// A default rather than a refusal, for the same reason the save root is: this is a fault-guarded
/// read of the game's own allocation, not a product setting, and answering 0 stops a file browser
/// from staging a single row -- which is what left the standalone **Load Character from File** row
/// inert with `cannot stage rows -- live ProfileSummary unavailable` (2026-09-11). A host that
/// tracks the allocation itself still overrides it.
///
/// The body reads the game's memory through `er-game-base`, which is a `cfg(windows)`-only
/// dependency, so the host build gets the same answer the windows build gives when there is no
/// game module: zero. Without the split, a host `cargo test -p er-quit-menu-core` fails to
/// compile on five unresolved paths rather than running the crate's tests.
#[cfg(not(windows))]
unsafe fn default_summary_ptr() -> usize {
    0
}

#[cfg(windows)]
unsafe fn default_summary_ptr() -> usize {
    let Ok(base) = er_game_base::mem::game_module_base() else {
        return 0;
    };
    let global = er_game_base::mem::game_data_addr(
        base,
        er_game_base::rva::GAME_DATA_MAN_GLOBAL_RVA,
        "GAME_DATA_MAN_GLOBAL_RVA",
    );
    let Some(game_data_man) = (unsafe { er_game_base::mem::safe_read_usize(global) }) else {
        return 0;
    };
    if game_data_man == 0 {
        return 0;
    }
    unsafe {
        er_game_base::mem::safe_read_usize(
            game_data_man + er_loading_portrait_core::layout::SLOT_MANAGER_CONTAINER_OFFSET,
        )
    }
    .unwrap_or(0)
}
fn default_slot_zero() -> i32 {
    0
}
fn default_slot_none() -> Option<i32> {
    None
}
unsafe fn default_build_table(_base: usize) -> bool {
    false
}
unsafe fn default_render_tick(_base: usize, _slot: i32) {}
unsafe fn default_loading_active(_base: usize) -> bool {
    false
}
fn default_window_none() -> usize {
    0
}
fn default_release_input() {}
fn default_take_bypass(_reason: &'static str) -> bool {
    false
}
unsafe fn default_dialog_from_action(_action_obj: usize) -> usize {
    0
}
/// The core owns the picker's snapshot, so it owns putting it back. This used to do nothing, and a
/// shell that staged browse rows into the live `CS::ProfileSummary` left them there for the rest of
/// the process -- visible on the title's `Load Game` list. A product overrides this with its own
/// ledger-aware restore; nothing else needs one.
#[cfg(windows)]
unsafe fn default_restore_profile_summary(reason: &str) {
    unsafe { crate::row_staging::restore_row_records(reason) };
}

/// There are no staged records to put back on a host build: `row_staging` is `#[cfg(windows)]`
/// because staging writes the game's own `CS::ProfileSummary` table.
#[cfg(not(windows))]
unsafe fn default_restore_profile_summary(_reason: &str) {}
fn default_arm_original(_save_path: &str) -> bool {
    false
}
fn default_no_pathbuf() -> Option<PathBuf> {
    None
}
unsafe fn default_ingest_save(_selected_path: &str) -> bool {
    false
}
/// Where the destination browser opens, what a new file there is called, and which file the
/// running session has loaded.
///
/// Built from three answers this seam already gives without a host: [`system_quit_env_save_path`],
/// [`system_quit_env_save_dir`] and [`default_save_root`]. A host that redirects the save writer
/// overrides it, because only that host knows which directory its redirect is using; a shell gets
/// the game's own container, which is the file it would be overwriting anyway.
///
/// Returning `None` here is what left the standalone **Save Game** row opening nothing in run
/// br-20260912-194412-7743: the flow reached stage 3, found no directory to browse, and timed out
/// 180 ticks later without writing the player's save.
#[cfg(windows)]
fn default_save_dest_origin() -> Option<SaveDestOrigin> {
    let save_path = match system_quit_env_save_path() {
        Ok(path) => path,
        Err(reason) => {
            append_autoload_debug(format_args!(
                "save-dest-picker: refused to open -- {reason}"
            ));
            return None;
        }
    };
    let loaded_path = PathBuf::from(crate::save_picker_menu::save_picker_windows_path_string(
        &save_path,
    ));
    let Some(loaded_file_name) = std::path::Path::new(&save_path)
        .file_name()
        .and_then(|name| name.to_str())
        .map(str::to_owned)
    else {
        append_autoload_debug(format_args!(
            "save-dest-picker: refused to open -- loaded save '{save_path}' has no file name"
        ));
        return None;
    };
    // Start where the loaded save lives; fall back to the save root only if that directory is gone.
    let start_dir = system_quit_env_save_dir()
        .ok()
        .map(|dir| {
            PathBuf::from(crate::save_picker_menu::save_picker_windows_path_string(
                &dir,
            ))
        })
        .filter(|dir| dir.is_dir())
        .or_else(|| {
            default_save_root()
                .and_then(|root| {
                    root.to_str()
                        .map(crate::save_picker_menu::save_picker_windows_path_string)
                })
                .map(PathBuf::from)
                .filter(|root| root.is_dir())
        });
    let Some(start_dir) = start_dir else {
        append_autoload_debug(format_args!(
            "save-dest-picker: refused to open -- neither the loaded save's directory nor the save root is readable"
        ));
        return None;
    };
    Some(SaveDestOrigin {
        start_dir,
        loaded_file_name,
        loaded_path,
    })
}
/// A host build has no game save directory to browse, and the Wine path translation the windows
/// arm above runs every candidate through lives in the `#[cfg(windows)]` `save_picker_menu`.
/// `None` is what the seam already means by "no destination": the picker opens nothing.
#[cfg(not(windows))]
fn default_save_dest_origin() -> Option<SaveDestOrigin> {
    None
}
fn default_reset_caret_latch() {}
unsafe fn default_import_applied() {}
fn default_windows_path_for_log(path: &str) -> String {
    path.to_owned()
}

// The three seams below are wired straight to a `#[cfg(windows)]` module on the game target.
// Each needs a host stand-in for the same reason the neutral defaults above exist: an un-hosted
// crate must never authorise a real save, and on a host build there is no game to authorise it
// against. They are the crate's own no-ops, not a weaker version of the real thing.
#[cfg(windows)]
use crate::save_dest_commit_runtime::save_dest_set_target as default_save_dest_set_target;
#[cfg(windows)]
use crate::save_flow_boxes::install_save_flow_msgbox_builder_capture as default_install_msgbox_builder_capture;
#[cfg(windows)]
use crate::save_picker_menu::save_picker_stage_row_records as default_save_picker_stage_row_records;

/// There is no `CS::MessageBoxDialog` builder to detour off the game target.
#[cfg(not(windows))]
fn default_install_msgbox_builder_capture() {}

/// No `CreateFileW` detour reads the destination window on a host build, so arming one would
/// record a target nothing can act on.
#[cfg(not(windows))]
fn default_save_dest_set_target(_path: PathBuf, _reason: &'static str) {}

/// Staging writes the game's own `CS::ProfileSummary` records; `false` is the seam's existing
/// answer for "the rows were not staged".
#[cfg(not(windows))]
unsafe fn default_save_picker_stage_row_records(
    _model: &er_save_picker_core::SavePickerModel,
) -> bool {
    false
}

impl QuitMenuHost {
    /// Neutral defaults: no-op logging, no save source, no summary, no slots, no window,
    /// and no save-write bypass -- an un-hosted crate must never be able to authorise a
    /// real save.
    pub const fn defaults() -> Self {
        Self {
            append_autoload_debug: default_log,
            append_crash_log: default_log,
            save_redirect_native_source_dir: default_no_save_redirect,
            install_msgbox_builder_capture: default_install_msgbox_builder_capture,
            default_save_root: default_no_root,
            save_picker_seamless_mode_after_settle: default_seamless,
            system_quit_env_save_path: default_no_save_path,
            system_quit_env_save_dir: default_no_save_dir,
            normalize_save_bytes_to_active_steam_id: default_normalize,
            system_quit_profile_summary_ptr: default_summary_ptr,
            portrait_loaded_slot: default_slot_zero,
            portrait_loaded_slot_confirmed: default_slot_none,
            portrait_target_slot: default_slot_zero,
            maybe_build_profile_table_for_loading: default_build_table,
            force_profile_render_tick: default_render_tick,
            native_loading_screen_active: default_loading_active,
            game_main_window: default_window_none,
            release_input_block_now: default_release_input,
            take_save_write_bypass: default_take_bypass,
            product_autoload_enabled: default_gate_off,
            switch_reload_active: default_gate_off,
            os_native_picker_active: default_gate_off,
            windows_path_for_log: default_windows_path_for_log,
            system_dialog_from_action_obj: default_dialog_from_action,
            system_quit_save_swap_restore_profile_summary: default_restore_profile_summary,
            system_quit_save_swap_arm_original: default_arm_original,
            save_picker_start_dir: default_no_pathbuf,
            system_quit_ingest_picked_save: default_ingest_save,
            save_dest_start_dir: default_save_dest_origin,
            save_dest_set_target: default_save_dest_set_target,
            save_picker_stage_row_records: default_save_picker_stage_row_records,
            reset_path_editor_caret_latch: default_reset_caret_latch,
            build_import_applied: default_import_applied,
        }
    }
}

impl Default for QuitMenuHost {
    fn default() -> Self {
        Self::defaults()
    }
}

static DEFAULT_HOST: QuitMenuHost = QuitMenuHost::defaults();
static HOST: OnceLock<QuitMenuHost> = OnceLock::new();

/// Install the host seam once, at DLL attach, before any hook install or task spawn can
/// run moved code. Returns false (and changes nothing) if a host was already installed.
pub fn install_host(host: QuitMenuHost) -> bool {
    HOST.set(host).is_ok()
}

fn host() -> &'static QuitMenuHost {
    HOST.get().unwrap_or(&DEFAULT_HOST)
}

// --- crate-internal wrappers bearing the exact original product names -----------------

#[allow(dead_code)]
pub(crate) fn append_autoload_debug(args: std::fmt::Arguments<'_>) {
    (host().append_autoload_debug)(args)
}
#[allow(dead_code)]
pub(crate) fn append_crash_log(args: std::fmt::Arguments<'_>) {
    (host().append_crash_log)(args)
}
#[allow(dead_code)]
pub(crate) fn default_save_root() -> Option<std::path::PathBuf> {
    (host().default_save_root)()
}
#[allow(dead_code)]
pub(crate) fn save_picker_seamless_mode_after_settle(reason: &str) -> bool {
    (host().save_picker_seamless_mode_after_settle)(reason)
}
#[allow(dead_code)]
pub(crate) fn system_quit_env_save_path() -> Result<String, &'static str> {
    (host().system_quit_env_save_path)()
}
#[allow(dead_code)]
pub(crate) fn system_quit_env_save_dir() -> Result<String, &'static str> {
    (host().system_quit_env_save_dir)()
}
#[allow(dead_code)]
pub(crate) fn normalize_save_bytes_to_active_steam_id(bytes: &mut [u8]) -> bool {
    (host().normalize_save_bytes_to_active_steam_id)(bytes)
}
#[allow(dead_code)]
pub(crate) unsafe fn system_quit_profile_summary_ptr() -> usize {
    unsafe { (host().system_quit_profile_summary_ptr)() }
}
#[allow(dead_code)]
pub(crate) fn portrait_loaded_slot() -> i32 {
    (host().portrait_loaded_slot)()
}
#[allow(dead_code)]
pub(crate) fn portrait_loaded_slot_confirmed() -> Option<i32> {
    (host().portrait_loaded_slot_confirmed)()
}
#[allow(dead_code)]
pub(crate) fn portrait_target_slot() -> i32 {
    (host().portrait_target_slot)()
}
#[allow(dead_code)]
pub(crate) unsafe fn maybe_build_profile_table_for_loading(base: usize) -> bool {
    unsafe { (host().maybe_build_profile_table_for_loading)(base) }
}
#[allow(dead_code)]
pub(crate) unsafe fn force_profile_render_tick(base: usize, slot: i32) {
    unsafe { (host().force_profile_render_tick)(base, slot) }
}
#[allow(dead_code)]
pub(crate) unsafe fn native_loading_screen_active(base: usize) -> bool {
    unsafe { (host().native_loading_screen_active)(base) }
}
#[allow(dead_code)]
pub(crate) fn game_main_window() -> usize {
    (host().game_main_window)()
}
#[allow(dead_code)]
pub(crate) fn release_input_block_now() {
    (host().release_input_block_now)()
}
#[allow(dead_code)]
pub(crate) fn take_save_write_bypass(reason: &'static str) -> bool {
    (host().take_save_write_bypass)(reason)
}
#[allow(dead_code)]
pub(crate) fn product_autoload_enabled() -> bool {
    (host().product_autoload_enabled)()
}
#[allow(dead_code)]
pub(crate) fn switch_reload_active() -> bool {
    (host().switch_reload_active)()
}
#[allow(dead_code)]
pub(crate) fn os_native_picker_active() -> bool {
    (host().os_native_picker_active)()
}
#[allow(dead_code)]
pub(crate) fn system_quit_windows_path_for_log(path: &str) -> String {
    (host().windows_path_for_log)(path)
}
#[allow(dead_code)]
pub(crate) unsafe fn system_dialog_from_action_obj(action_obj: usize) -> usize {
    unsafe { (host().system_dialog_from_action_obj)(action_obj) }
}
#[allow(dead_code)]
pub(crate) unsafe fn system_quit_save_swap_restore_profile_summary(reason: &str) {
    unsafe { (host().system_quit_save_swap_restore_profile_summary)(reason) }
}
#[allow(dead_code)]
pub(crate) fn system_quit_save_swap_arm_original(save_path: &str) -> bool {
    (host().system_quit_save_swap_arm_original)(save_path)
}
#[allow(dead_code)]
pub(crate) fn save_picker_start_dir() -> Option<PathBuf> {
    (host().save_picker_start_dir)()
}
#[allow(dead_code)]
pub(crate) unsafe fn system_quit_ingest_picked_save(selected_path: &str) -> bool {
    unsafe { (host().system_quit_ingest_picked_save)(selected_path) }
}
#[allow(dead_code)]
pub(crate) fn save_dest_start_dir() -> Option<SaveDestOrigin> {
    (host().save_dest_start_dir)()
}
#[allow(dead_code)]
pub(crate) fn save_dest_set_target(path: PathBuf, source: &'static str) {
    (host().save_dest_set_target)(path, source)
}
#[allow(dead_code)]
/// # Safety
/// The model must stay alive across the call, and the host must be installed.
pub(crate) unsafe fn save_picker_stage_row_records(
    model: &er_save_picker_core::SavePickerModel,
) -> bool {
    unsafe { (host().save_picker_stage_row_records)(model) }
}
#[allow(dead_code)]
pub(crate) fn reset_path_editor_caret_latch() {
    (host().reset_path_editor_caret_latch)()
}
#[allow(dead_code)]
pub(crate) unsafe fn build_import_applied() {
    unsafe { (host().build_import_applied)() }
}

/// Ask the host to install the builder capture. Idempotent by contract.
///
/// Its only caller is the save flow, which is `#[cfg(windows)]`.
#[cfg(windows)]
pub(crate) fn install_msgbox_builder_capture() {
    (host().install_msgbox_builder_capture)()
}

/// A host that does not redirect the save writer.
fn default_no_save_redirect() -> Option<std::path::PathBuf> {
    None
}

/// Where the host redirects the game's save writer, if it does.
///
/// Its only caller is the destination commit, which is `#[cfg(windows)]`.
#[cfg(windows)]
pub(crate) fn save_redirect_native_source_dir() -> Option<std::path::PathBuf> {
    (host().save_redirect_native_source_dir)()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unhosted_quit_menu_can_never_authorise_a_real_save() {
        // The single most dangerous default in this seam: with no host, the one-shot
        // save-write bypass must be refused, so a crate loaded without a product cannot
        // push a write past `er-save-suppress`.
        assert!(!take_save_write_bypass("test"));
    }

    #[test]
    fn an_unhosted_quit_menu_reports_no_save_source_rather_than_a_guess() {
        // The save path is now discovered rather than refused, because refusing it stops a file
        // browser from opening at all -- but discovery only ever finds the game's own
        // `%APPDATA%/EldenRing/<steamid>/ER0000.*`, and on a machine with no such directory it
        // still errors. What must never be guessed is the write authorisation above.
        let discovered = system_quit_env_save_path();
        if let Ok(path) = &discovered {
            assert!(
                path.contains("EldenRing"),
                "discovered a save outside the game's own folder: {path}"
            );
            assert!(system_quit_env_save_dir().is_ok());
        }
        assert_eq!(game_main_window(), 0);
    }
}
