//! A private Scaleform cache key for the picker's `05_010_ProfileSelect`, so the title's **Load
//! Game** keeps the movie FromSoft ships.
//!
//! # The bleed this closes
//!
//! `er_gfx::title_05_010::stats_panel` does not decorate the ProfileSelect movie, it re-lays it
//! out: the 128x128 face box goes alpha-0, `PlayerName` and the Level cluster shift left into the
//! freed strip, and the row stack is compacted from five 156px rows in a 780px viewport to ten
//! 52px rows, scrollbar and mask included. That is the right movie for a save-destination browser
//! and the wrong movie for choosing a character at the title.
//!
//! Both surfaces are one asset. `05_010_profileselect.gfx` is opened **once** per process, during
//! title boot -- run br-20260913-010107-6f60 recorded the single serve as file-open hit #70, before
//! the player had pressed anything -- and Scaleform then answers every later open from its cache,
//! keyed by the URL string. So a swap installed at file-open cannot be aimed at one surface: by the
//! time either is on screen the decision is long made, and a runtime latch around the open would
//! never be read.
//!
//! # What a second key buys
//!
//! A key Scaleform has not seen is a cache miss, and a miss is a fresh file-open. That is already
//! how the picker's path field and the Quit tab's link field get two different derivations of one
//! `02_990_textinput` (see [`crate::software_keyboard::TEXT_INPUT_RESOURCE_NAME`]) -- and those two
//! name files that do not exist on disk, which is why [`crate::gfx_swap`] redirects the miss to the
//! canonical bytes rather than letting the loader hunt for them.
//!
//! The keyboard supplies its own resource name through a config struct it builds. Nobody builds the
//! ProfileSelect one: `FUN_14081f6f0` ([`PROFILE_SELECT_WRAPPER_RVA`]) writes the literal
//! `L"05_010_ProfileSelect"` into a stack `CSScaleformLoadInfo` and hands it to the window-job
//! constructor at [`MENU_WINDOW_JOB_BUILD_RVA`], which copies `filename` into the job. Detouring the
//! constructor and rewriting that one field, only while this crate's own picker is the thing
//! opening, gives the picker its own key and leaves every other opener -- the title's Load Game
//! included -- on the game's own.
//!
//! # Why the swap can refuse
//!
//! The redirect target is the canonical URL captured from the game's own vanilla open, not a string
//! written down here. Until that capture has happened the custom key names nothing at all, so the
//! swap declines and the picker shares the vanilla movie exactly as it did before this module
//! existed. A shell that never sees the boot load is degraded, not broken.

use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::mem::{game_rva_for_hook, safe_read_u16, safe_read_usize};

use crate::host::append_autoload_debug;

/// `CS::MenuWindowJob`'s constructor, which every ProfileSelect opener funnels through.
///
/// Two callers reach it with the ProfileSelect load info: the wrapper at
/// [`er_title_flow::PROFILE_SELECT_WRAPPER_RVA`] the picker fires directly, and `FUN_1407fb050`, which copies a
/// caller's `CSScaleformLoadInfo` onto its own stack first. Detouring the constructor rather than
/// either caller means the rewrite happens once, in the one place both arrive.
// The address is `er_title_flow`'s to declare: it hooks the same constructor passively as
// `MENU_WINDOW_JOB_NATIVE_CTOR_B_RVA`. Deriving the name here rather than re-spelling the
// literal keeps one value for one function, which `scripts/check-rva-alias-drift.py` enforces.
const MENU_WINDOW_JOB_BUILD_RVA: u32 = er_title_flow::MENU_WINDOW_JOB_NATIVE_CTOR_B_RVA;

/// `CSScaleformLoadInfo::filename`, a `wchar_t*`. The struct is 16 bytes: an 8-byte `MenuJobResult`
/// and this.
const SCALEFORM_LOAD_INFO_FILENAME_OFFSET: usize = 0x8;

/// The game's own resource name for the character-select window.
pub const NATIVE_PROFILE_SELECT_RESOURCE_NAME: &str = "05_010_ProfileSelect";

/// The picker's private cache key. Names no file: [`crate::gfx_swap`] redirects its open to the
/// canonical `05_010` payload and derives the picker movie from those bytes.
pub const PICKER_PROFILE_SELECT_RESOURCE_NAME: &str = "05_010_ProfileSelect_SavePicker";

/// NUL-terminated UTF-16 copy of an ASCII resource name. Const-evaluated, so a name too long for
/// its buffer is a compile error rather than an unterminated string handed to the engine.
const fn utf16_resource<const N: usize>(name: &str) -> [u16; N] {
    let bytes = name.as_bytes();
    assert!(bytes.len() < N, "resource name needs room for its NUL");
    let mut out = [0u16; N];
    let mut index = 0;
    while index < bytes.len() {
        out[index] = bytes[index] as u16;
        index += 1;
    }
    out
}

// `static`, not `const`, for the reason `er-quit-menu-core::software_keyboard` spells out in full:
// the constructor copies the pointer into the job and the movie is loaded from it much later, so
// the bytes have to outlive the call. A `const` is a temporary at each use site.
static PICKER_PROFILE_SELECT_RESOURCE: [u16; 32] =
    utf16_resource(PICKER_PROFILE_SELECT_RESOURCE_NAME);

/// Non-zero while this crate's picker is inside its own ProfileSelect submit.
///
/// A count rather than a flag: the submit is synchronous on the menu thread, but a nested open
/// would otherwise have its inner scope clear the outer one's arm.
static PICKER_SUBMIT_DEPTH: AtomicUsize = AtomicUsize::new(0);

/// Swaps performed, and opens that arrived armed but were left alone.
static KEY_SWAPS: AtomicUsize = AtomicUsize::new(0);
static KEY_SWAPS_DECLINED: AtomicUsize = AtomicUsize::new(0);

static BUILD_ORIG: AtomicUsize = AtomicUsize::new(0);
static BUILD_INSTALLED: AtomicUsize = AtomicUsize::new(0);

/// Arm the rewrite for the duration of one picker submit.
///
/// The guard clears the arm when it drops, including on an early return out of the submit, so a
/// refused open cannot leave the next opener -- which may well be the title's Load Game -- armed.
pub struct PickerSubmitArm {
    _private: (),
}

impl PickerSubmitArm {
    /// Arm the rewrite. Held across the native submit call and dropped immediately after.
    pub fn new() -> Self {
        PICKER_SUBMIT_DEPTH.fetch_add(1, Ordering::SeqCst);
        Self { _private: () }
    }
}

impl Default for PickerSubmitArm {
    fn default() -> Self {
        Self::new()
    }
}

impl Drop for PickerSubmitArm {
    fn drop(&mut self) {
        let _ = PICKER_SUBMIT_DEPTH.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |depth| {
            Some(depth.saturating_sub(1))
        });
    }
}

/// Whether the private key is being served. False means every ProfileSelect open keeps the game's
/// own resource name, which is what a host that wants the derived movie everywhere wants.
pub fn picker_profile_select_key_installed() -> bool {
    BUILD_INSTALLED.load(Ordering::SeqCst) != 0
}

/// How many opens were rewritten, and how many armed opens were declined.
pub fn picker_profile_select_key_counts() -> (usize, usize) {
    (
        KEY_SWAPS.load(Ordering::SeqCst),
        KEY_SWAPS_DECLINED.load(Ordering::SeqCst),
    )
}

/// Compare a NUL-terminated UTF-16 string against ASCII, bounded and fault-safe.
///
/// # Safety
///
/// No precondition on the address: every read goes through the fault-safe reader, so an unmapped
/// pointer answers `false` rather than faulting.
unsafe fn wide_equals_ascii(text: usize, want: &str) -> bool {
    if text == 0 {
        return false;
    }
    for (index, byte) in want.bytes().enumerate() {
        match unsafe { safe_read_u16(text + index * 2) } {
            Some(unit) if unit == u16::from(byte) => {}
            _ => return false,
        }
    }
    let terminator = unsafe { safe_read_u16(text + want.len() * 2) };
    terminator == Some(0)
}

/// `CS::MenuWindowJob`'s constructor. Rewrites the ProfileSelect resource name to the picker's own
/// key while a picker submit is in flight, and forwards every other call untouched.
///
/// # Safety
///
/// Installed by `er-hook` and called by the game on its menu thread. `info` is the caller's own
/// stack `CSScaleformLoadInfo`, so writing its `filename` is scoped to this one construction.
unsafe extern "system" fn menu_window_job_build_hook(
    job_out: usize,
    callback: usize,
    info: usize,
    context: usize,
) -> usize {
    let orig = BUILD_ORIG.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    if PICKER_SUBMIT_DEPTH.load(Ordering::SeqCst) != 0 && info != 0 {
        let filename_slot = info + SCALEFORM_LOAD_INFO_FILENAME_OFFSET;
        let filename = unsafe { safe_read_usize(filename_slot) }.unwrap_or(0);
        if unsafe { wide_equals_ascii(filename, NATIVE_PROFILE_SELECT_RESOURCE_NAME) } {
            if crate::gfx_swap::profile_select_canonical_url_captured() {
                // Safety: `info` is the caller's stack struct, already read through the fault-safe
                // reader at this exact offset, and the replacement outlives the process.
                unsafe {
                    core::ptr::write(
                        filename_slot as *mut usize,
                        PICKER_PROFILE_SELECT_RESOURCE.as_ptr() as usize,
                    )
                };
                let swaps = KEY_SWAPS.fetch_add(1, Ordering::SeqCst) + 1;
                append_autoload_debug(format_args!(
                    "system-quit-gfx: ProfileSelect open #{swaps} rebound to the picker's own cache key '{PICKER_PROFILE_SELECT_RESOURCE_NAME}'; the title's Load Game keeps '{NATIVE_PROFILE_SELECT_RESOURCE_NAME}'"
                ));
            } else {
                let declined = KEY_SWAPS_DECLINED.fetch_add(1, Ordering::SeqCst) + 1;
                append_autoload_debug(format_args!(
                    "system-quit-gfx: ProfileSelect open #{declined} kept the game's own cache key -- the canonical 05_010 url has not been seen yet, so a private key would name nothing"
                ));
            }
        }
    }
    // Safety: the union publishes either the game trampoline or the next handler; both take these
    // four arguments.
    let next: er_hook::UnionFn = unsafe { std::mem::transmute(orig) };
    let built = unsafe { next(job_out, callback, info, context) };
    // Which job every ProfileSelect open produced, armed or not. The private cache key is proven to
    // hand the picker its own file -- run br-20260913-151808-4457 logged the canonical open as file
    // 0x1dbe3d80 and the picker's as a different object -- and the title's Load Game still came up
    // wearing the picker's row geometry. A separate file is not a separate window, so this records
    // the job each open built: two opens answering with one job is a single instance both surfaces
    // share, and then every write the picker makes to it outlives the picker.
    if info != 0 {
        let filename =
            unsafe { safe_read_usize(info + SCALEFORM_LOAD_INFO_FILENAME_OFFSET) }.unwrap_or(0);
        let is_picker = unsafe { wide_equals_ascii(filename, PICKER_PROFILE_SELECT_RESOURCE_NAME) };
        let is_native = unsafe { wide_equals_ascii(filename, NATIVE_PROFILE_SELECT_RESOURCE_NAME) };
        if is_picker || is_native {
            let job = unsafe { safe_read_usize(job_out) }.unwrap_or(0);
            append_autoload_debug(format_args!(
                "system-quit-gfx: ProfileSelect window built key={} job_out=0x{job_out:x} job=0x{job:x} ret=0x{built:x} armed={}",
                if is_picker { "picker" } else { "canonical" },
                PICKER_SUBMIT_DEPTH.load(Ordering::SeqCst)
            ));
        }
    }
    built
}

/// Detour the window-job constructor so this crate's picker opens ProfileSelect under its own key.
///
/// Only a host that wants the title's **Load Game** left vanilla calls this. The product does not:
/// its stats panel is a feature of the character-select screen, so it serves the derived movie on
/// the game's own key and wants it on both surfaces.
///
/// # Safety
///
/// Process attach or startup-hook context.
pub unsafe fn install_picker_profile_select_key() -> bool {
    if BUILD_INSTALLED
        .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return true;
    }
    let Ok(addr) = game_rva_for_hook(MENU_WINDOW_JOB_BUILD_RVA) else {
        append_autoload_debug(format_args!(
            "system-quit-gfx: failed to resolve the MenuWindowJob constructor rva 0x{MENU_WINDOW_JOB_BUILD_RVA:x}; the picker will share the title's ProfileSelect movie"
        ));
        BUILD_INSTALLED.store(0, Ordering::SeqCst);
        return false;
    };
    match unsafe { er_hook::register_shared_hook(addr, menu_window_job_build_hook, &BUILD_ORIG) } {
        Ok(_route) => {
            append_autoload_debug(format_args!(
                "system-quit-gfx: registered the MenuWindowJob constructor 0x{addr:x} on the union; the picker's ProfileSelect opens under '{PICKER_PROFILE_SELECT_RESOURCE_NAME}' and the title's Load Game keeps the game's own movie"
            ));
            true
        }
        Err(status) => {
            append_autoload_debug(format_args!(
                "system-quit-gfx: register_shared_hook MenuWindowJob constructor failed: {status:?}; the picker will share the title's ProfileSelect movie"
            ));
            BUILD_INSTALLED.store(0, Ordering::SeqCst);
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_private_key_is_the_native_name_with_a_suffix() {
        assert!(
            PICKER_PROFILE_SELECT_RESOURCE_NAME.starts_with(NATIVE_PROFILE_SELECT_RESOURCE_NAME)
        );
        assert_ne!(
            PICKER_PROFILE_SELECT_RESOURCE_NAME,
            NATIVE_PROFILE_SELECT_RESOURCE_NAME
        );
    }

    #[test]
    fn the_wide_resource_is_nul_terminated_and_holds_the_whole_name() {
        let name: String = String::from_utf16(
            &PICKER_PROFILE_SELECT_RESOURCE[..PICKER_PROFILE_SELECT_RESOURCE_NAME.len()],
        )
        .expect("ascii round-trips through utf-16");
        assert_eq!(name, PICKER_PROFILE_SELECT_RESOURCE_NAME);
        assert_eq!(
            PICKER_PROFILE_SELECT_RESOURCE[PICKER_PROFILE_SELECT_RESOURCE_NAME.len()],
            0
        );
    }

    #[test]
    fn the_arm_is_scoped_to_its_guard() {
        assert_eq!(PICKER_SUBMIT_DEPTH.load(Ordering::SeqCst), 0);
        {
            let _outer = PickerSubmitArm::new();
            assert_eq!(PICKER_SUBMIT_DEPTH.load(Ordering::SeqCst), 1);
            {
                let _inner = PickerSubmitArm::new();
                assert_eq!(PICKER_SUBMIT_DEPTH.load(Ordering::SeqCst), 2);
            }
            assert_eq!(PICKER_SUBMIT_DEPTH.load(Ordering::SeqCst), 1);
        }
        assert_eq!(PICKER_SUBMIT_DEPTH.load(Ordering::SeqCst), 0);
    }
}
