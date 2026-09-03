//! Make a PICKED save's `CS::ProfileSummary` real at the title, so the native Continue row can
//! load it the same way it loads the default save.
//!
//! # Why a picked save had no summary
//!
//! The records the Load-Character rows and `profile_slot_fingerprint` read come from the save
//! container's own `USER_DATA010` table, parsed by `CS::ProfileSummary::Deserialize`
//! (`0x140261f00` -> `0x140261f10`: ten occupancy bytes into `summary+0x8`, then ten records of
//! `0x2a0` from `summary+0x18` via `0x140261cf0`). The game runs that exactly once, inside the boot
//! common-data load (`0x1402570c0`, reached from the save-data `ShowProgressJob`'s delegate).
//!
//! At a BOOT picker that read comes free: the DLL holds the save-data job in `Continue` until the
//! user picks, then passes it through, and the delegate reads the redirected container. A LATE pick
//! -- the boot check accepted a save, the autoload could not load it, and the picker armed
//! afterwards -- has no such job left to run, so the summary keeps describing the save that was
//! rejected, or nothing at all.
//!
//! # What this does instead
//!
//! `MarkProfileIndexAsUsed` (`0x140262250`) is NOT the answer and was checked before this was
//! written: its entire body is `if (slot < 10) { saveSlotsStates[slot] = true; return true; }`. It
//! sets an occupancy FLAG and touches no record field, so a slot it marks still fingerprints empty
//! and the native Continue row still has nothing to load.
//!
//! Rather than re-enter the boot job (an async container read plus a full common-data parse that
//! would also re-deserialize GameSettings, the key config and the net-penalty state at the title),
//! this rebuilds the records directly from the staged container's own bytes, through the SAME
//! writer the System>Quit foreign-save preview already uses -- name, level, play time, rune memory,
//! map, `PlaceName`, `FaceData` and `ChrAsm` per slot, occupancy included. The staged container is
//! what the game itself will read, so the records and the bodies agree by construction.
//!
//! It deliberately does NOT call the profile-renderer refresh: that AVs when the renderer table has
//! not been built yet (`0x9aa6d4`, observed), and at the boot title it has not.

use core::sync::atomic::Ordering;

use er_game_base::mem::game_module_base;
use er_game_base::profile_summary::PROFILE_SUMMARY_TOTAL_BYTES;

use crate::host::{
    active_save_file_for_system_quit, append_autoload_debug, direct_save_file_source_active,
    load_profile_slot_caches_from_bytes, native_fullread_slot,
};
use crate::live_records::{profile_slot_fingerprint, system_quit_profile_summary_ptr};
use crate::refresh_policy::{RefreshStep, refresh_step};
use crate::save_bytes_records::write_profile_summary_records_from_save_bytes;

/// A null game pointer -- the product's `TITLE_OWNER_SCAN_START_ADDRESS`, which is `usize::MIN`.
const NULL_SUMMARY: usize = usize::MIN;

/// [`PICKED_SUMMARY_REFRESH_STATE`]: the game's own boot save-data read already populated it.
pub const PICKED_SUMMARY_STATE_NATIVE_READ: usize = 1;
/// [`PICKED_SUMMARY_REFRESH_STATE`]: this DLL re-read the staged container at the title.
pub const PICKED_SUMMARY_STATE_REREAD: usize = 2;

pub use er_telemetry_core::counters::PICKED_SUMMARY_REFRESH_ATTEMPTS;
pub use er_telemetry_core::counters::PICKED_SUMMARY_REFRESH_SLOT_MASK;
pub use er_telemetry_core::counters::PICKED_SUMMARY_REFRESH_STATE;
pub use er_telemetry_core::counters::PICKED_SUMMARY_REFRESH_TICKS;

/// Does the slot a direct-file (picked / loose `save_file`) source will load hold a REAL character
/// in the live `CS::ProfileSummary`?
///
/// This is [`profile_slot_fingerprint`] -- level >= 1 and a non-empty name in the RECORD -- and
/// deliberately not `saveSlotsStates[slot]`, which says only that something once marked the slot.
/// The slot is resolved with [`native_fullread_slot`] so the predicate and the full-read fallback
/// can never disagree about which slot is being talked about.
pub fn direct_source_slot_summary_real() -> bool {
    if !direct_save_file_source_active() {
        return false;
    }
    unsafe { profile_slot_fingerprint(native_fullread_slot()).0 }
}

/// Re-read the picked container's `CS::ProfileSummary` records at the title. Idempotent and
/// self-throttling; safe to call from every autoload tick.
///
/// Returns true once the summary describes the picked container -- whether the game's own boot read
/// did it or this did.
pub fn refresh_direct_source_profile_summary() -> bool {
    if !direct_save_file_source_active() {
        return false;
    }
    let state = PICKED_SUMMARY_REFRESH_STATE.load(Ordering::SeqCst);
    if state != 0 {
        return true;
    }
    if direct_source_slot_summary_real() {
        // The boot picker's ordinary path: the held save-data ShowProgressJob passed through after
        // the pick and the game read the redirected container itself. Nothing to rebuild.
        PICKED_SUMMARY_REFRESH_STATE.store(PICKED_SUMMARY_STATE_NATIVE_READ, Ordering::SeqCst);
        append_autoload_debug(format_args!(
            "picked-summary: live ProfileSummary already describes the picked container (slot {} fingerprints real) -- native Continue row is usable, no re-read needed",
            native_fullread_slot()
        ));
        return true;
    }
    let tick = PICKED_SUMMARY_REFRESH_TICKS.fetch_add(1, Ordering::SeqCst);
    let attempts = PICKED_SUMMARY_REFRESH_ATTEMPTS.load(Ordering::SeqCst);
    let RefreshStep::Attempt(attempt) = refresh_step(tick, attempts) else {
        return false;
    };
    PICKED_SUMMARY_REFRESH_ATTEMPTS.store(attempt, Ordering::SeqCst);
    unsafe { attempt_profile_summary_reread(attempt) }
}

/// One re-read attempt. Every early return logs why, because a silent failure here is what puts the
/// deserialize back at the title.
unsafe fn attempt_profile_summary_reread(attempt: usize) -> bool {
    const NULL: usize = NULL_SUMMARY;
    let Ok(base) = game_module_base() else {
        append_autoload_debug(format_args!(
            "picked-summary: attempt {attempt} deferred -- game module base unresolved"
        ));
        return false;
    };
    let summary = unsafe { system_quit_profile_summary_ptr() };
    if summary == NULL {
        append_autoload_debug(format_args!(
            "picked-summary: attempt {attempt} deferred -- GameDataMan -> ProfileSummary not allocated yet"
        ));
        return false;
    }
    // The STAGED native save, never the user's source file: the staged copy is what the game's own
    // reads resolve to, so the records this builds and the bodies the game will deserialize come
    // from the same container. `active_save_file_for_system_quit` returns exactly that for a direct
    // source (it never hands back `SAVE_DIRECT_SOURCE_FILE`, which is read-only).
    let Some(path) = active_save_file_for_system_quit() else {
        append_autoload_debug(format_args!(
            "picked-summary: attempt {attempt} deferred -- no staged active save path resolvable yet"
        ));
        return false;
    };
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(err) => {
            append_autoload_debug(format_args!(
                "picked-summary: attempt {attempt} FAILED -- staged container '{}' unreadable: {err}",
                path.display()
            ));
            return false;
        }
    };
    if er_save_loader::bnd4::parse_entries(&bytes).is_err() {
        append_autoload_debug(format_args!(
            "picked-summary: attempt {attempt} FAILED -- staged container '{}' ({} bytes) is not a readable BND4",
            path.display(),
            bytes.len()
        ));
        return false;
    }
    // Captured BEFORE the rewrite: the writer uses it as a structural template for any slot whose
    // visual blocks it cannot locate in the container.
    let snapshot =
        unsafe { core::slice::from_raw_parts(summary as *const u8, PROFILE_SUMMARY_TOTAL_BYTES) }
            .to_vec();
    let (mask, _stats) =
        unsafe { write_profile_summary_records_from_save_bytes(base, summary, &snapshot, &bytes) };
    if mask == 0 {
        append_autoload_debug(format_args!(
            "picked-summary: attempt {attempt} FAILED -- staged container '{}' has no readable character slots; the native Continue row stays unusable",
            path.display()
        ));
        return false;
    }
    // The rows and the loading-screen stats line read these caches, not the records, so a rebuilt
    // summary with stale caches shows the picked character's level under the previous save's name.
    let decoded = load_profile_slot_caches_from_bytes(&bytes, "autoload picked save");
    PICKED_SUMMARY_REFRESH_SLOT_MASK.store(mask, Ordering::SeqCst);
    let slot = native_fullread_slot();
    let (real, map, level, name_len) = unsafe { profile_slot_fingerprint(slot) };
    if !real {
        append_autoload_debug(format_args!(
            "picked-summary: attempt {attempt} rewrote slots mask=0x{mask:x} from '{}' but target slot {slot} still fingerprints empty (map=0x{map:x} level={level} name_len={name_len}) -- the picked slot is not in this container",
            path.display()
        ));
        return false;
    }
    PICKED_SUMMARY_REFRESH_STATE.store(PICKED_SUMMARY_STATE_REREAD, Ordering::SeqCst);
    append_autoload_debug(format_args!(
        "picked-summary: *** RE-READ the picked container's ProfileSummary at the title *** path='{}' attempt={attempt} slots=0x{mask:x} caches={decoded}/10; target slot {slot} now map=0x{map:x} level={level} name_len={name_len} -- the picked save takes the NATIVE Continue row from here, so no deserialize runs at the title",
        path.display()
    ));
    true
}
