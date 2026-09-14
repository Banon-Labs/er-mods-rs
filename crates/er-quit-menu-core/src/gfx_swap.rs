//! The two Scaleform movies the System>Quit rows stand on, derived in place from the game's own
//! vanilla payload.
//!
//! Moved out of `er-quickload`'s `experiments/startup_hooks/loading_cover/profile_table_gfx_files.rs`,
//! which still serves the loading cover, the title `05_000` and the profile table `05_010` from the
//! same file-open observer. Only the quit-menu half is here, because only the quit-menu half is
//! what a standalone quit-menu shell has to serve when the product is absent:
//!
//! * `02_040_optionsetting` -- the Quit Game panel's grid. Vanilla ships two cells and the mod's
//!   rows are cells three through six, so a shell that clones rows without this swap clones into
//!   cells that do not exist;
//! * `02_990_textinput` -- the link field the **Load Build from URL** row opens, under its own
//!   Scaleform cache key.
//!
//! # One deriver, by construction
//!
//! `er_gfx::options_02_040::quit6` fail-closes when its input is not the vanilla movie
//! (`Quit6Error::KnownInputBadOutput`), so a second deriver handed already-derived bytes correctly
//! refuses. That is why `scripts/me3-dll-conflicts.toml` pairs `er-quit-menu` with `er-quickload`
//! as a duplicate owner rather than a shared hook: whichever of them is loaded is the sole deriver,
//! and they are never loaded together.

use std::sync::OnceLock;
use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::mem::{game_data_addr, safe_read_i32, safe_read_u8, safe_read_usize};

use crate::host::append_autoload_debug;

/// `Scaleform::MemoryFile`, the three fields a derive-and-swap rewrites.
const SCALEFORM_MEMORY_FILE_DATA_OFFSET: usize = 0x18;
const SCALEFORM_MEMORY_FILE_LEN_OFFSET: usize = 0x20;
const SCALEFORM_MEMORY_FILE_CURSOR_OFFSET: usize = 0x24;

/// A pointer value that is never a live object. Spelled as a named constant because a bare `0`
/// beside a heap address reads as arithmetic rather than as a sentinel.
const NOT_A_POINTER: usize = usize::MIN;

/// The canonical vanilla payload both 02_990 cache keys are redirected to, so the game's own shared
/// `02_990` cache entry is never mutated.
pub static TEXT_INPUT_02_990_CANONICAL_URL: &[u8] = b"data0:/menu/win/02_990_textinput.gfx\0";

static OPTIONS_02_040_QUIT6_EDITED: OnceLock<Vec<u8>> = OnceLock::new();
static OPTIONS_02_040_QUIT6_SERVES: AtomicUsize = AtomicUsize::new(0);
static OPTIONS_02_040_QUIT6_FAILURES: AtomicUsize = AtomicUsize::new(0);

static TEXT_INPUT_02_990_INLINE_EDITED: OnceLock<Vec<u8>> = OnceLock::new();
static TEXT_INPUT_02_990_INLINE_SERVES: AtomicUsize = AtomicUsize::new(0);
static TEXT_INPUT_02_990_INLINE_FAILURES: AtomicUsize = AtomicUsize::new(0);

static BUILD_URL_02_990_EDITED: OnceLock<Vec<u8>> = OnceLock::new();
static BUILD_URL_02_990_SERVES: AtomicUsize = AtomicUsize::new(0);
static BUILD_URL_02_990_FAILURES: AtomicUsize = AtomicUsize::new(0);

/// How many times each derivation has been served, for the telemetry line a run reads back.
pub fn gfx_swap_serves() -> (usize, usize, usize) {
    (
        OPTIONS_02_040_QUIT6_SERVES.load(Ordering::SeqCst),
        TEXT_INPUT_02_990_INLINE_SERVES.load(Ordering::SeqCst),
        BUILD_URL_02_990_SERVES.load(Ordering::SeqCst),
    )
}

/// Where one derivation's cache, counters, log tag and transform live together.
///
/// Two cache keys reach the 02_990 path (`02_990_TextInput_PathEditor` and
/// `02_990_TextInput_BuildUrl`), each redirected to the same canonical vanilla payload and each
/// deriving a different movie from it. Sharing one derivation is what put an unstyled link field in
/// the corner of the screen, so the two are kept apart by construction rather than by a flag.
struct Derivation {
    cache: &'static OnceLock<Vec<u8>>,
    serves: &'static AtomicUsize,
    failures: &'static AtomicUsize,
    tag: &'static str,
    derive: fn(&[u8]) -> Result<Vec<u8>, String>,
}

/// Write a derived payload over a `MemoryFile`'s data pointer, length and cursor.
///
/// # Safety
///
/// `file` must be a live `Scaleform::MemoryFile` whose vtable the caller has already checked, and
/// `edited` must live for the process lifetime -- the engine keeps the pointer.
unsafe fn install_payload(file: usize, edited: &'static [u8]) {
    unsafe {
        core::ptr::write(
            (file + SCALEFORM_MEMORY_FILE_DATA_OFFSET) as *mut usize,
            edited.as_ptr() as usize,
        );
        core::ptr::write(
            (file + SCALEFORM_MEMORY_FILE_LEN_OFFSET) as *mut u32,
            edited.len() as u32,
        );
        core::ptr::write((file + SCALEFORM_MEMORY_FILE_CURSOR_OFFSET) as *mut u32, 0);
    }
}

/// True when `file` is a `Scaleform::MemoryFile` of the running build.
///
/// # Safety
///
/// `file` is read through the fault-safe readers, so a wild value is a `false` rather than a fault.
unsafe fn memory_file_vtable_matches(base: usize, file: usize) -> bool {
    let want = game_data_addr(
        base,
        er_game_base::rva::SCALEFORM_MEMORY_FILE_VTABLE_RVA,
        "SCALEFORM_MEMORY_FILE_VTABLE_RVA",
    );
    unsafe { safe_read_usize(file) }.unwrap_or(0) == want
}

/// Derive-and-swap one 02_990 `MemoryFile` in place, fail-closed onto the untouched native payload.
///
/// # Safety
///
/// Called from the Scaleform file-open prologue with the file the native loader just returned.
unsafe fn text_input_02_990_swap(base: usize, file: usize, derivation: &Derivation) -> bool {
    let tag = derivation.tag;
    let fail = |reason: core::fmt::Arguments<'_>| {
        derivation.failures.fetch_add(1, Ordering::SeqCst);
        append_autoload_debug(format_args!("{tag}: 02_990 GFX edit FAIL-CLOSED: {reason}"));
        false
    };
    if file == 0 || file == NOT_A_POINTER {
        return fail(format_args!("invalid MemoryFile 0x{file:x}"));
    }
    if !unsafe { memory_file_vtable_matches(base, file) } {
        return fail(format_args!(
            "unexpected MemoryFile vtable 0x{:x}",
            unsafe { safe_read_usize(file) }.unwrap_or(0)
        ));
    }
    let edited = match derivation.cache.get() {
        Some(edited) => edited,
        None => {
            let data =
                unsafe { safe_read_usize(file + SCALEFORM_MEMORY_FILE_DATA_OFFSET) }.unwrap_or(0);
            let len =
                unsafe { safe_read_i32(file + SCALEFORM_MEMORY_FILE_LEN_OFFSET) }.unwrap_or(0);
            if data == 0 || data == NOT_A_POINTER || !(64..=0x0010_0000).contains(&len) {
                return fail(format_args!(
                    "implausible payload data=0x{data:x} len={len}"
                ));
            }
            // Safety: the length came from the file the native loader just populated, and the
            // vtable check above establishes that this really is a `MemoryFile`.
            let vanilla = unsafe { core::slice::from_raw_parts(data as *const u8, len as usize) };
            match (derivation.derive)(vanilla) {
                Ok(edited) => {
                    append_autoload_debug(format_args!(
                        "{tag}: derived 02_990 GFX in={} out={} fnv=0x{:016x}",
                        vanilla.len(),
                        edited.len(),
                        er_gfx::fnv1a64(&edited)
                    ));
                    derivation.cache.get_or_init(|| edited)
                }
                Err(error) => return fail(format_args!("{error}")),
            }
        }
    };
    unsafe { install_payload(file, edited) };
    derivation.serves.fetch_add(1, Ordering::SeqCst);
    true
}

/// Inline `02_990_textinput` over the save picker's CurrentPath field. Gated by the path editor's
/// own cache key at the file-open observer, so ordinary game text inputs retain vanilla geometry.
///
/// # Safety
///
/// See [`text_input_02_990_swap`].
pub unsafe fn text_input_02_990_swap_to_inline(base: usize, file: usize) -> bool {
    unsafe {
        text_input_02_990_swap(
            base,
            file,
            &Derivation {
                cache: &TEXT_INPUT_02_990_INLINE_EDITED,
                serves: &TEXT_INPUT_02_990_INLINE_SERVES,
                failures: &TEXT_INPUT_02_990_INLINE_FAILURES,
                tag: "save-picker-path",
                derive: |vanilla| {
                    er_gfx::text_input_02_990::inline_current_path_editor(vanilla)
                        .map_err(|error| error.to_string())
                },
            },
        )
    }
}

/// Centre `02_990_textinput` over the Quit tab for the **Load Build from URL** link field, with the
/// movie's own backing plate and frame art kept and widened to hold a planner link.
///
/// # Safety
///
/// See [`text_input_02_990_swap`].
pub unsafe fn text_input_02_990_swap_to_build_url(base: usize, file: usize) -> bool {
    unsafe {
        text_input_02_990_swap(
            base,
            file,
            &Derivation {
                cache: &BUILD_URL_02_990_EDITED,
                serves: &BUILD_URL_02_990_SERVES,
                failures: &BUILD_URL_02_990_FAILURES,
                tag: "system-quit-build-url",
                derive: |vanilla| {
                    er_gfx::build_url_02_990::centered_build_url_editor(vanilla)
                        .map_err(|error| error.to_string())
                        // Read the dim back out of the payload this is about to install, so a
                        // derivation that lost it is a counter rather than an undimmed field.
                        .and_then(crate::build_url_backdrop::attest_derived_build_url_backdrop)
                },
            },
        )
    }
}

/// Inline the picker's own path field into `02_990_textinput` for the **current path** editor.
///
/// The path editor submits its own Scaleform cache key, `02_990_TextInput_PathEditor`, so that it
/// can be derived separately from the link field: this derivation alpha-zeroes the movie's backing
/// plate and both frame placements, because over ProfileSelect the picker's own `CurrentPath`
/// button already supplies the frame.
///
/// A key with no file behind it is why the field came up blank. Scaleform caches by that string and
/// there is no `02_990_TextInput_PathEditor` on disk, so the redirect to the canonical payload is
/// not an optimisation -- without it the editor opens onto nothing, which is what the player saw on
/// run br-20260912-204014-00a5: the current path read `Z:\` on the picker, and the field went empty
/// the moment it was entered. The product redirected both keys from its own file-open observer; a
/// shell installs none, so this crate has to redirect its own.
///
/// # Safety
///
/// See [`text_input_02_990_swap`].
pub unsafe fn text_input_02_990_swap_to_path_editor(base: usize, file: usize) -> bool {
    unsafe {
        text_input_02_990_swap(
            base,
            file,
            &Derivation {
                cache: &PATH_EDITOR_02_990_EDITED,
                serves: &PATH_EDITOR_02_990_SERVES,
                failures: &PATH_EDITOR_02_990_FAILURES,
                tag: "save-picker-path",
                derive: |vanilla| {
                    er_gfx::text_input_02_990::inline_current_path_editor(vanilla)
                        .map_err(|error| error.to_string())
                },
            },
        )
    }
}

static PATH_EDITOR_02_990_EDITED: std::sync::OnceLock<Vec<u8>> = std::sync::OnceLock::new();
static PATH_EDITOR_02_990_SERVES: AtomicUsize = AtomicUsize::new(0);
static PATH_EDITOR_02_990_FAILURES: AtomicUsize = AtomicUsize::new(0);

/// Six-cell `02_040_optionsetting` runtime edit for System>Quit. This mirrors the 05_000/05_010
/// `MemoryFile` swap path, but deliberately has no env or file-backed diagnostic input: the product
/// must not ship or depend on an external GFx. The derived movie is built from the game's own
/// vanilla payload and cached for process lifetime so the native `MemoryFile`'s data pointer
/// remains valid.
///
/// # Safety
///
/// Called from the Scaleform file-open prologue with the file the native loader just returned.
pub unsafe fn options_02_040_quit6_swap_to_edited(base: usize, file: usize) -> bool {
    if file == 0 || file == NOT_A_POINTER {
        return false;
    }
    let fail = |reason: core::fmt::Arguments<'_>| {
        OPTIONS_02_040_QUIT6_FAILURES.fetch_add(1, Ordering::SeqCst);
        append_autoload_debug(format_args!(
            "system-quit-gfx: 02_040 quit6 runtime edit FAIL-CLOSED (serving native vanilla): {reason}"
        ));
        false
    };
    if !unsafe { memory_file_vtable_matches(base, file) } {
        return fail(format_args!(
            "unexpected file vtable 0x{:x} (want MemoryFile 0x{:x})",
            unsafe { safe_read_usize(file) }.unwrap_or(0),
            game_data_addr(
                base,
                er_game_base::rva::SCALEFORM_MEMORY_FILE_VTABLE_RVA,
                "SCALEFORM_MEMORY_FILE_VTABLE_RVA"
            )
        ));
    }
    let edited = match OPTIONS_02_040_QUIT6_EDITED.get() {
        Some(cached) => cached,
        None => {
            let data =
                unsafe { safe_read_usize(file + SCALEFORM_MEMORY_FILE_DATA_OFFSET) }.unwrap_or(0);
            let len =
                unsafe { safe_read_i32(file + SCALEFORM_MEMORY_FILE_LEN_OFFSET) }.unwrap_or(0);
            if data == 0 || data == NOT_A_POINTER || !(64..=0x0100_0000).contains(&len) {
                return fail(format_args!(
                    "implausible payload data=0x{data:x} len={len}"
                ));
            }
            let len = len as usize;
            let magic_ok = unsafe { safe_read_u8(data) } == Some(b'G')
                && unsafe { safe_read_u8(data + 1) } == Some(b'F')
                && unsafe { safe_read_u8(data + 2) } == Some(b'X')
                && unsafe { safe_read_u8(data + len - 1) }.is_some();
            if !magic_ok {
                return fail(format_args!(
                    "payload at 0x{data:x} len={len} is unreadable or not GFX-magic"
                ));
            }
            // Safety: the length and the magic bytes were both read back through the fault-safe
            // readers before this slice is formed.
            let vanilla = unsafe { core::slice::from_raw_parts(data as *const u8, len) };
            let known = er_gfx::options_02_040::is_known_vanilla_win(vanilla);
            match er_gfx::options_02_040::quit6(vanilla) {
                Ok(out) => {
                    let out_fnv = er_gfx::fnv1a64(&out);
                    // `in_fnv` is logged because `known_vanilla` comes back false on this path and
                    // the pair is what would arm it. The fingerprint in `er_gfx` was taken from the
                    // unpacked file; the loader hands us a payload 9 bytes longer, so the length
                    // check fails and the derived output is never compared against its golden hash
                    // -- a changed movie would be edited blind and served. Pinning the runtime
                    // input's own length and fnv as a second accepted fingerprint closes that, and
                    // this line is where the number to pin comes from.
                    let in_fnv = er_gfx::fnv1a64(vanilla);
                    append_autoload_debug(format_args!(
                        "system-quit-gfx: 02_040 quit6 runtime edit derived in={len} in_fnv=0x{in_fnv:016x} out={} known_vanilla={known} out_fnv=0x{out_fnv:016x}",
                        out.len()
                    ));
                    OPTIONS_02_040_QUIT6_EDITED.get_or_init(|| out)
                }
                Err(err) => {
                    return fail(format_args!("in={len} known_vanilla={known}: {err}"));
                }
            }
        }
    };
    unsafe { install_payload(file, edited) };
    OPTIONS_02_040_QUIT6_SERVES.fetch_add(1, Ordering::SeqCst);
    true
}

/// `05_010_ProfileSelect` runtime edit: derive the stats-panel movie the picker's chrome stands on
/// from the native `MemoryFile`'s own vanilla payload, cache it, and install it in place.
///
/// # Why a shell needs this at all
///
/// The row-populate detour dresses a browse row only when the row proxy has an `ErCharStats` child
/// (`profile_row_chrome::row_is_stats_panel_template`), and that field exists only in this derived
/// movie. A host that hooks the populate without serving the movie therefore dresses nothing: every
/// row is scored foreign and left native, which run br-20260912-201935-ad27 recorded 13 times as
/// `has no ErCharStats child -- not our ProfileSelect movie; left native` while the picker on screen
/// rendered in the game's own vanilla presentation.
///
/// The product derives the same movie from its own file-open observer. Two derivers are not a race:
/// `er_gfx::title_05_010::stats_panel` fail-closes on already-derived input, which is why the pair
/// is a `duplicate-owner` row in `scripts/me3-dll-conflicts.toml`.
///
/// # Safety
///
/// As [`options_02_040_quit6_swap_to_edited`]: `file` is the loader's return value, and every read
/// below goes through the fault-safe readers.
pub unsafe fn profile_05_010_swap_to_edited(base: usize, file: usize) -> bool {
    if file == 0 || file == NOT_A_POINTER {
        return false;
    }
    let fail = |reason: core::fmt::Arguments<'_>| {
        PROFILE_05_010_FAILURES.fetch_add(1, Ordering::SeqCst);
        append_autoload_debug(format_args!(
            "system-quit-gfx: 05_010 stats-panel runtime edit FAIL-CLOSED (serving native vanilla): {reason}"
        ));
        false
    };
    if !unsafe { memory_file_vtable_matches(base, file) } {
        return fail(format_args!(
            "unexpected file vtable 0x{:x}",
            unsafe { safe_read_usize(file) }.unwrap_or(0)
        ));
    }
    let edited = match PROFILE_05_010_EDITED.get() {
        Some(cached) => cached,
        None => {
            let data =
                unsafe { safe_read_usize(file + SCALEFORM_MEMORY_FILE_DATA_OFFSET) }.unwrap_or(0);
            let len =
                unsafe { safe_read_i32(file + SCALEFORM_MEMORY_FILE_LEN_OFFSET) }.unwrap_or(0);
            if data == 0 || data == NOT_A_POINTER || !(64..=0x0100_0000).contains(&len) {
                return fail(format_args!(
                    "implausible payload data=0x{data:x} len={len}"
                ));
            }
            let len = len as usize;
            let magic_ok = unsafe { safe_read_u8(data) } == Some(b'G')
                && unsafe { safe_read_u8(data + 1) } == Some(b'F')
                && unsafe { safe_read_u8(data + 2) } == Some(b'X')
                && unsafe { safe_read_u8(data + len - 1) }.is_some();
            if !magic_ok {
                return fail(format_args!(
                    "payload at 0x{data:x} len={len} is unreadable or not GFX-magic"
                ));
            }
            // Safety: the length and the magic bytes were both read back through the fault-safe
            // readers before this slice is formed.
            let vanilla = unsafe { core::slice::from_raw_parts(data as *const u8, len) };
            let known = er_gfx::title_05_010::is_known_vanilla(vanilla);
            match er_gfx::title_05_010::stats_panel(vanilla) {
                Ok(out) => {
                    let out_fnv = er_gfx::fnv1a64(&out);
                    let validated = out.len() == er_gfx::title_05_010::EDITED_LEN
                        && out_fnv == er_gfx::title_05_010::EDITED_FNV1A64;
                    append_autoload_debug(format_args!(
                        "system-quit-gfx: 05_010 stats-panel runtime edit derived in={len} out={} known_vanilla={known} validated={validated} out_fnv=0x{out_fnv:016x}",
                        out.len()
                    ));
                    PROFILE_05_010_EDITED.get_or_init(|| out)
                }
                Err(err) => {
                    return fail(format_args!("in={len} known_vanilla={known}: {err}"));
                }
            }
        }
    };
    unsafe { install_payload(file, edited) };
    PROFILE_05_010_SERVES.fetch_add(1, Ordering::SeqCst);
    true
}

/// The file object the canonical `05_010_profileselect` open was answered with, and how many times
/// the picker's private key was answered with that same object. See the compare at the open site.
static PROFILE_05_010_CANONICAL_FILE: AtomicUsize = AtomicUsize::new(0);
static PROFILE_05_010_SHARED_OBJECT: AtomicUsize = AtomicUsize::new(0);

/// How many picker-key opens came back as the title's own movie object.
pub fn profile_05_010_shared_object_count() -> usize {
    PROFILE_05_010_SHARED_OBJECT.load(Ordering::SeqCst)
}

static PROFILE_05_010_EDITED: std::sync::OnceLock<Vec<u8>> = std::sync::OnceLock::new();
static PROFILE_05_010_FAILURES: AtomicUsize = AtomicUsize::new(0);
static PROFILE_05_010_SERVES: AtomicUsize = AtomicUsize::new(0);

// ---- serving the two movies from a standalone shell ------------------------------------------

/// Which movies this host wants served.
///
/// The Quit grid is the one that must not be served unconditionally. Vanilla's tab has two cells
/// and the six-cell derivation is for cloned rows; a host that clones none gets a widened grid with
/// four empty cells (bd `slim-quickload-still-widened-the-quit-grid-2026-09-12`). The Save Game row
/// replaces a row that already exists, so it needs the picker movie and not the grid.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct GfxServeSet {
    /// The six-cell `02_040_optionsetting` Quit grid. Only a host that clones rows into cells three
    /// through six.
    pub quit_grid: bool,
    /// The link field's `02_990_textinput` movie, under its own cache key.
    pub build_url_field: bool,
    /// The picker's current-path editor, under `02_990_TextInput_PathEditor`. Belongs to whoever
    /// opens the picker, not to whoever armed the link field: they are two derivations of one movie.
    pub path_editor_field: bool,
    /// The `05_010_ProfileSelect` stats-panel movie the picker chrome stands on, served on the
    /// game's own cache key -- so the title's **Load Game** gets the derived layout too. What a
    /// host whose feature *is* the character-select screen wants.
    pub profile_select: bool,
    /// The same movie, served only under
    /// [`PICKER_PROFILE_SELECT_RESOURCE_NAME`](crate::profile_select_movie_key::PICKER_PROFILE_SELECT_RESOURCE_NAME).
    /// A host that sets this leaves the game's own key vanilla, so its picker is re-laid out and
    /// the title's Load Game is not. Needs
    /// [`install_picker_profile_select_key`](crate::profile_select_movie_key::install_picker_profile_select_key)
    /// to rebind the open, and the two are armed together or neither does anything.
    pub profile_select_picker_key: bool,
}

impl GfxServeSet {
    /// Everything this module can derive, with the picker's movie on the game's own cache key.
    ///
    /// Reserved for a host whose feature *is* the character-select screen. Every other host wants
    /// [`Self::ALL_PICKER_KEYED`]: the derived movie re-lays out ProfileSelect, and on that key it
    /// re-lays out the title's **Load Game** as well.
    pub const ALL: Self = Self {
        quit_grid: true,
        build_url_field: true,
        path_editor_field: true,
        profile_select: true,
        profile_select_picker_key: false,
    };
    /// Only the picker movie: a host that replaces a vanilla row rather than cloning new ones.
    pub const PROFILE_SELECT_ONLY: Self = Self {
        quit_grid: false,
        build_url_field: false,
        // The path editor is part of the picker, so a picker-only host still needs it.
        path_editor_field: true,
        profile_select: true,
        profile_select_picker_key: false,
    };
    /// Every movie a full row set needs, with the title's **Load Game** left as the game ships it.
    ///
    /// The same derivations as [`Self::ALL`]; only the key the picker's ProfileSelect is reached
    /// through differs. What a shell wants: its browse rows are dressed and character select is
    /// not compacted behind its back.
    pub const ALL_PICKER_KEYED: Self = Self {
        quit_grid: true,
        build_url_field: true,
        path_editor_field: true,
        profile_select: false,
        profile_select_picker_key: true,
    };
    /// The picker's movies, with the title's **Load Game** left exactly as the game ships it.
    ///
    /// The same derivation as [`Self::PROFILE_SELECT_ONLY`], reached through the picker's own
    /// cache key instead of the game's. What a host wants when its picker browses save
    /// destinations and has no business re-laying out character select.
    pub const PICKER_KEYED: Self = Self {
        quit_grid: false,
        build_url_field: false,
        path_editor_field: true,
        profile_select: false,
        profile_select_picker_key: true,
    };
}

/// What the installed hook serves. Bits are added, never removed: two hosts in one process each
/// need their own movies, and the hook is installed once.
static SERVE_QUIT_GRID: AtomicUsize = AtomicUsize::new(0);
static SERVE_BUILD_URL_FIELD: AtomicUsize = AtomicUsize::new(0);
static SERVE_PATH_EDITOR_FIELD: AtomicUsize = AtomicUsize::new(0);
static SERVE_PROFILE_SELECT: AtomicUsize = AtomicUsize::new(0);
static SERVE_PROFILE_SELECT_PICKER_KEY: AtomicUsize = AtomicUsize::new(0);

/// The url the game itself opens `05_010_profileselect.gfx` under, copied out of the one vanilla
/// open so the picker's private cache key has somewhere real to be redirected.
///
/// Captured rather than written down. The key names no file, so its open has to be pointed at the
/// canonical payload the way both 02_990 keys are -- and the game's own string is the only spelling
/// guaranteed to be the one its loader accepts on this build.
static CANONICAL_PROFILE_SELECT_URL: OnceLock<Vec<u8>> = OnceLock::new();

/// Whether the canonical `05_010` url has been seen. Until it has, rebinding an open to the private
/// key would name nothing at all, so
/// [`crate::profile_select_movie_key`] declines the rebind and the picker shares the vanilla movie.
pub fn profile_select_canonical_url_captured() -> bool {
    CANONICAL_PROFILE_SELECT_URL.get().is_some()
}

/// Copy a NUL-terminated ASCII url out of the loader's own buffer, bounded.
///
/// # Safety
///
/// No precondition on the address: every read goes through the fault-safe reader.
unsafe fn capture_canonical_profile_select_url(url: usize) {
    if url == 0 || CANONICAL_PROFILE_SELECT_URL.get().is_some() {
        return;
    }
    const MAX: usize = 512;
    let mut bytes = Vec::with_capacity(64);
    for offset in 0..MAX {
        match unsafe { safe_read_u8(url + offset) } {
            Some(0) | None => break,
            Some(byte) => bytes.push(byte),
        }
    }
    if bytes.is_empty() {
        return;
    }
    bytes.push(0);
    let text = String::from_utf8_lossy(&bytes[..bytes.len() - 1]).into_owned(); // UTF-8 Lossy: a log line naming a game path, never parsed back.
    if CANONICAL_PROFILE_SELECT_URL.set(bytes).is_ok() {
        append_autoload_debug(format_args!(
            "system-quit-gfx: canonical 05_010 url captured as '{text}'; the picker's private cache key can be redirected to it"
        ));
    }
}

/// This module's slot in the `er-hook` union chain for the Scaleform file-open prologue.
static FILE_OPEN_ORIG: AtomicUsize = AtomicUsize::new(0);
static FILE_OPEN_INSTALLED: AtomicUsize = AtomicUsize::new(0);
static FILE_OPEN_HITS: AtomicUsize = AtomicUsize::new(0);

/// Scan the NUL-terminated ASCII path at `url` for `needle`, case-insensitively and bounded.
///
/// # Safety
///
/// No precondition on the address: every read goes through the fault-safe reader, so an unmapped
/// pointer answers `false` rather than faulting.
unsafe fn bounded_ascii_contains(url: usize, needle: &[u8]) -> bool {
    if url == 0 || needle.is_empty() {
        return false;
    }
    const MAX: usize = 512;
    let mut buf = [0u8; MAX];
    let mut n = 0usize;
    while n < MAX {
        match unsafe { safe_read_u8(url + n) } {
            Some(0) | None => break,
            Some(b) => {
                buf[n] = b.to_ascii_lowercase();
                n += 1;
            }
        }
    }
    if n < needle.len() {
        return false;
    }
    buf[..n].windows(needle.len()).any(|w| w == needle)
}

/// Serve the Quit tab's grid and the link field's movie, chained onto whatever else detours the
/// Scaleform file-open prologue.
///
/// Union-shaped, not game-shaped: this prologue is already detoured by `er-armament-icons`, and two
/// MinHook instances on one prologue overwrite each other's trampolines -- measured, with the
/// product reporting `installed = true` and zero hits for a whole session while every GFx swap it
/// owns went silently vanilla. The game passes three arguments; the fourth register is ignored and
/// the flags word is forwarded unchanged.
///
/// # Safety
///
/// Installed by `er-hook` and called by the game on its own loader thread.
unsafe extern "system" fn quit_menu_scaleform_file_open_hook(
    loader: usize,
    url: usize,
    flags_reg: usize,
    _unused: usize,
) -> usize {
    let orig = FILE_OPEN_ORIG.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    let hit = FILE_OPEN_HITS.fetch_add(1, Ordering::SeqCst) + 1;
    let is_options_02_040 = SERVE_QUIT_GRID.load(Ordering::SeqCst) != 0
        && unsafe { bounded_ascii_contains(url, b"02_040_optionsetting") };
    let is_build_url_02_990 = SERVE_BUILD_URL_FIELD.load(Ordering::SeqCst) != 0
        && unsafe { bounded_ascii_contains(url, b"02_990_textinput_buildurl") };
    let is_path_editor_02_990 = SERVE_PATH_EDITOR_FIELD.load(Ordering::SeqCst) != 0
        && unsafe { bounded_ascii_contains(url, b"02_990_textinput_patheditor") };
    // The private key contains the native key as a prefix, so the two are told apart on the string
    // before either serve bit is consulted -- otherwise a host serving the game's own key would
    // also claim the picker's.
    let has_picker_key = unsafe { bounded_ascii_contains(url, b"05_010_profileselect_savepicker") };
    let is_native_05_010 =
        !has_picker_key && unsafe { bounded_ascii_contains(url, b"05_010_profileselect") };
    if is_native_05_010 {
        // Every host captures it, whether or not it serves this key: the capture is what lets a
        // private key exist at all, and the one vanilla open is where the url can be read.
        unsafe { capture_canonical_profile_select_url(url) };
    }
    let is_picker_05_010 =
        has_picker_key && SERVE_PROFILE_SELECT_PICKER_KEY.load(Ordering::SeqCst) != 0;
    let is_profile_05_010 = is_native_05_010 && SERVE_PROFILE_SELECT.load(Ordering::SeqCst) != 0;
    // A custom cache key forces a fresh Scaleform load. Redirect only that key's file-open to the
    // canonical native movie; the game's shared 02_990 cache entry stays untouched.
    // Both 02_990 keys name a file that does not exist: they are cache keys chosen so the two
    // fields get separate derivations of one movie. The redirect is what gives either of them any
    // bytes at all.
    let open_url = if is_build_url_02_990 || is_path_editor_02_990 {
        TEXT_INPUT_02_990_CANONICAL_URL.as_ptr() as usize
    } else if is_picker_05_010 && let Some(canonical) = CANONICAL_PROFILE_SELECT_URL.get() {
        // Same trade as the two 02_990 keys: the key is a cache miss on purpose and names no file,
        // so the open is pointed at the payload the game itself loaded.
        canonical.as_ptr() as usize
    } else {
        url
    };
    // Safety: the union publishes either the game trampoline (three arguments, the extra register
    // harmlessly ignored) or the next handler, which is four-argument. Calling a chained handler
    // with the game's narrower signature would leave its fourth register undefined.
    let next: er_hook::UnionFn = unsafe { std::mem::transmute(orig) };
    let native = unsafe { next(loader, open_url, flags_reg, 0) };
    // Which file object each 05_010 key was handed, because the private key is only a per-surface
    // gate if the loader answers it with a different object. The key's open is redirected back to
    // the canonical url (the key names no file), so the loader is free to answer out of the cache
    // entry the title's Load Game already holds -- and `install_payload` then rewrites the movie
    // both surfaces draw. That is what the user photographed on run br-20260913-150909-6701: the
    // picker's row geometry on the title's Load Game. A pointer compare is what tells a fresh load
    // from a cache hit, and it costs one store on a path that runs a few times per boot.
    if is_native_05_010 {
        PROFILE_05_010_CANONICAL_FILE.store(native, Ordering::SeqCst);
    }
    // Every 05_010 open, both keys, with the object the loader answered with. The pointer compare
    // below can only speak when the canonical open passed through this hook, and on run
    // br-20260913-151533-5edf it never spoke -- which leaves two unseparated explanations, a fresh
    // object or a canonical open this hook never saw. One line per open separates them.
    if is_native_05_010 || has_picker_key {
        append_autoload_debug(format_args!(
            "system-quit-gfx: 05_010 open #{hit} key={} serve_bits(native={} picker={}) -> file=0x{native:x} canonical_seen=0x{:x}",
            if has_picker_key {
                "picker"
            } else {
                "canonical"
            },
            SERVE_PROFILE_SELECT.load(Ordering::SeqCst),
            SERVE_PROFILE_SELECT_PICKER_KEY.load(Ordering::SeqCst),
            PROFILE_05_010_CANONICAL_FILE.load(Ordering::SeqCst),
        ));
    }
    if is_picker_05_010 {
        let canonical = PROFILE_05_010_CANONICAL_FILE.load(Ordering::SeqCst);
        if canonical != 0 && canonical == native {
            PROFILE_05_010_SHARED_OBJECT.fetch_add(1, Ordering::SeqCst);
            append_autoload_debug(format_args!(
                "system-quit-gfx: 05_010 picker key was answered with the title's own movie object 0x{native:x} -- a cache hit, not a fresh load, so editing it re-lays out the title's Load Game as well. Serving vanilla for this open."
            ));
        }
    }
    if !(is_options_02_040
        || is_build_url_02_990
        || is_path_editor_02_990
        || is_profile_05_010
        || is_picker_05_010)
    {
        return native;
    }
    let Ok(base) = er_game_base::mem::game_module_base() else {
        return native;
    };
    // The game asking for a 02_990 key is its own statement that a new field is being built, which
    // is the signal that retires the previous field's window. Both keys carry it.
    if is_build_url_02_990 || is_path_editor_02_990 {
        crate::software_keyboard::build_url_note_movie_served();
    }
    let shared_with_title = is_picker_05_010
        && PROFILE_05_010_CANONICAL_FILE.load(Ordering::SeqCst) == native
        && native != 0;
    let served = if is_options_02_040 {
        unsafe { options_02_040_quit6_swap_to_edited(base, native) }
    } else if shared_with_title {
        // Fail closed rather than re-lay out a surface this host does not own.
        false
    } else if is_profile_05_010 || is_picker_05_010 {
        unsafe { profile_05_010_swap_to_edited(base, native) }
    } else if is_path_editor_02_990 {
        unsafe { text_input_02_990_swap_to_path_editor(base, native) }
    } else {
        unsafe { text_input_02_990_swap_to_build_url(base, native) }
    };
    append_autoload_debug(format_args!(
        "system-quit-gfx: served {} movie #{hit} loader=0x{loader:x} ret=0x{native:x} redirected_to_canonical_02_990={is_build_url_02_990} memory_replacement={served}",
        if is_options_02_040 {
            "02_040_optionsetting"
        } else if is_picker_05_010 {
            "05_010_profileselect (the picker's own cache key)"
        } else if is_profile_05_010 {
            "05_010_profileselect"
        } else if is_path_editor_02_990 {
            "02_990_textinput_patheditor"
        } else {
            "02_990_textinput_buildurl"
        }
    ));
    native
}

/// Detour the Scaleform file-open prologue so this module serves the two movies the rows stand on.
///
/// Only a host that owns the derivation calls this. The product does not: it already detours the
/// same prologue for the loading cover, the title `05_000` and the profile table, and serves these
/// two from that handler. Two derivers of one movie is not a race to be won -- `quit6` fail-closes
/// on already-derived input by design -- which is why the pair is a `duplicate-owner` conflict in
/// `scripts/me3-dll-conflicts.toml` rather than a shared hook.
///
/// # Safety
///
/// Process attach or startup-hook context, before the title has loaded its movies.
pub unsafe fn install_quit_menu_gfx_swap_hook() -> bool {
    unsafe { install_gfx_swap_hook_for(GfxServeSet::ALL_PICKER_KEYED) }
}

/// The movies the hook will actually swap, named for the log.
///
/// Written from the latches rather than from the caller's set, because the set is additive across
/// hosts: the line has to describe what this process now serves, not what the last caller asked
/// for. It replaced a hard-coded "the six-cell Quit grid and the link field's movie", which went on
/// claiming both after the serve set arrived -- run br-20260912-202348-248f printed exactly that
/// while the host had asked for the picker movie alone.
fn served_movie_list() -> String {
    let mut names: Vec<&str> = Vec::new();
    if SERVE_QUIT_GRID.load(Ordering::SeqCst) != 0 {
        names.push("the six-cell Quit grid");
    }
    if SERVE_BUILD_URL_FIELD.load(Ordering::SeqCst) != 0 {
        names.push("the link field's movie");
    }
    if SERVE_PATH_EDITOR_FIELD.load(Ordering::SeqCst) != 0 {
        names.push("the path editor's movie");
    }
    if SERVE_PROFILE_SELECT.load(Ordering::SeqCst) != 0 {
        names.push("the 05_010 picker movie");
    }
    if SERVE_PROFILE_SELECT_PICKER_KEY.load(Ordering::SeqCst) != 0 {
        names.push("the 05_010 picker movie under the picker's own cache key, leaving the title's Load Game vanilla");
    }
    if names.is_empty() {
        return "nothing (no host asked for a movie)".to_owned();
    }
    names.join(" + ")
}

/// [`install_quit_menu_gfx_swap_hook`] for a host that needs only some of the movies.
///
/// The serve set is additive across callers: the hook is installed once, and a second host asking
/// for a different movie turns that one on without turning the first host's off.
///
/// # Safety
///
/// As [`install_quit_menu_gfx_swap_hook`].
pub unsafe fn install_gfx_swap_hook_for(serve: GfxServeSet) -> bool {
    if serve.quit_grid {
        SERVE_QUIT_GRID.store(1, Ordering::SeqCst);
    }
    if serve.build_url_field {
        SERVE_BUILD_URL_FIELD.store(1, Ordering::SeqCst);
    }
    if serve.path_editor_field {
        SERVE_PATH_EDITOR_FIELD.store(1, Ordering::SeqCst);
    }
    if serve.profile_select {
        SERVE_PROFILE_SELECT.store(1, Ordering::SeqCst);
    }
    if serve.profile_select_picker_key {
        SERVE_PROFILE_SELECT_PICKER_KEY.store(1, Ordering::SeqCst);
    }
    if FILE_OPEN_INSTALLED
        .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return true;
    }
    let rva = er_game_base::rva::TITLE_SCALEFORM_FILE_OPEN_RVA as u32;
    let Ok(addr) = er_game_base::mem::game_rva_for_hook(rva) else {
        append_autoload_debug(format_args!(
            "system-quit-gfx: failed to resolve the Scaleform file-open prologue rva 0x{rva:x}; the Quit grid stays vanilla and no rows will be visible"
        ));
        FILE_OPEN_INSTALLED.store(0, Ordering::SeqCst);
        return false;
    };
    // `register_shared_hook`, not `register_union_hook`, and the difference is the whole feature.
    // The local form installs this dll's own MinHook instance on the prologue. `er-quickload`
    // detours the same address for its title-resource observer, so with the product co-loaded the
    // two instances raced and this one lost: run br-20260913-022901-f395 logged the registration
    // and then not one `served` line, not even the canonical url capture, while the Quit tab came
    // up `cols=2 rows=1 navigable_cells=2` against `item_count=6` -- four cloned rows in the list
    // with no cell to be drawn or hit in. The shared form resolves `er_effects_union_register` out
    // of the product and chains into the union it already owns, and falls back to the local one
    // when the product is absent.
    match unsafe {
        er_hook::register_shared_hook(addr, quit_menu_scaleform_file_open_hook, &FILE_OPEN_ORIG)
    } {
        Ok(route) => {
            append_autoload_debug(format_args!(
                "system-quit-gfx: registered the Scaleform file-open prologue 0x{addr:x} on the {route:?} union; serving {}",
                served_movie_list()
            ));
            true
        }
        Err(status) => {
            append_autoload_debug(format_args!(
                "system-quit-gfx: register_shared_hook Scaleform file-open failed: {status:?}; the Quit grid stays vanilla and no rows will be visible"
            ));
            FILE_OPEN_INSTALLED.store(0, Ordering::SeqCst);
            false
        }
    }
}
