//! The snapshot that puts the game's character records back after the picker borrows them.
//!
//! The in-game file picker renders its browse rows by writing them into the live
//! `CS::ProfileSummary`: each 0x2a0-byte record is zeroed and the row label copied into the name
//! field. That destroys the game's own records -- face data at `+0x38`, `ChrAsm` at `+0x1a8`, level
//! at `+0x24`, map at `+0x30` -- so they have to be put back on every exit from the picker: close,
//! commit, and abort alike.
//!
//! # Why this is not the save-swap ledger
//!
//! It used to ride on the product's `preview_applied`/`committed` latches, and that conflation was
//! a bug with a visible symptom. Row staging and the foreign-save preview write the same
//! allocation but are different things, and only the preview may be suppressed by `committed` -- a
//! committed preview has to survive in order to be loaded. Sharing one bit meant an earlier
//! cross-file load commit set `committed`, which nothing resets except the tail of the restore it
//! was blocking, and every later picker left its rows in the records for the rest of the process:
//! loading screens rendered `[..] EldenRing` and `[ new ]` as character names.
//!
//! So the staging keeps its own latch and its own snapshot, restored unconditionally -- and now
//! its own module, in the crate that owns the picker, so a standalone shell has it without the
//! product's save-swap ledger behind it.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard, OnceLock};

use er_game_base::profile_summary::PROFILE_SUMMARY_TOTAL_BYTES;

/// The picker's borrow of the live records, and what it borrowed them from.
#[derive(Default)]
pub struct RowStaging {
    /// The live ProfileSummary currently holds picker browse-row labels, not the game's records.
    pub rows_staged: bool,
    /// The allocation `rows_snapshot` was taken from; a restore refuses to write anywhere else.
    pub rows_summary_ptr: usize,
    /// The `PROFILE_SUMMARY_TOTAL_BYTES` image of that allocation as it looked before the first
    /// staging of the current picker session.
    pub rows_snapshot: Vec<u8>,
}

static ROW_STAGING: OnceLock<Mutex<RowStaging>> = OnceLock::new();

/// Lock the staging state, recovering from a poisoned lock rather than panicking on the game
/// thread -- a panic here would take the process down inside a menu tick.
pub fn row_staging_lock() -> MutexGuard<'static, RowStaging> {
    ROW_STAGING
        .get_or_init(|| Mutex::new(RowStaging::default()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Whether the live records currently hold the picker's rows.
pub fn rows_staged() -> bool {
    row_staging_lock().rows_staged
}

/// Take the pre-staging image of `summary`, once per picker session.
///
/// # Safety
///
/// `summary` must be the live `CS::ProfileSummary` allocation: this reads
/// `PROFILE_SUMMARY_TOTAL_BYTES` from it through a raw pointer.
pub unsafe fn arm_row_snapshot(summary: usize) {
    let mut staging = row_staging_lock();
    if staging.rows_staged && staging.rows_summary_ptr == summary {
        return;
    }
    staging.rows_summary_ptr = summary;
    staging.rows_snapshot = unsafe {
        core::slice::from_raw_parts(summary as *const u8, PROFILE_SUMMARY_TOTAL_BYTES).to_vec()
    };
    staging.rows_staged = true;
}

/// Put the game's own records back over the picker's browse rows.
///
/// The core's answer, and therefore a standalone shell's: it needs no save-swap ledger, no preview
/// latch and no product. Until this existed the restore lived only behind
/// [`crate::host::QuitMenuHost`]'s `system_quit_save_swap_restore_profile_summary`, whose neutral
/// default did nothing at all -- so a shell staged browse rows into the live records and never took
/// them out. Quitting to the title then listed them as characters: run br-20260913-013205-c94d put
/// `[ new ]`, `[..] tmp` and `-home-banon/` on the `Load Game` screen at `Level 0`, beside the
/// player's real character.
///
/// # Why an unreadable summary is not a failure
///
/// `GameDataMan+0x78` reads as zero across the whole clean-title window. Consuming the latch on
/// that reading would throw away the only copy of the player's records over a pointer that is about
/// to come back, so it stays armed and the next exit point retries. A summary that reads as some
/// *other* allocation is a different matter: the one that was snapshotted is gone, so the image is
/// dropped rather than written into whatever now occupies the address.
///
/// # Safety
///
/// Menu or game thread, like every other writer of these records.
pub unsafe fn restore_row_records(reason: &str) -> bool {
    let mut staging = row_staging_lock();
    if !staging.rows_staged {
        return false;
    }
    let live = unsafe { crate::host::system_quit_profile_summary_ptr() };
    let target = staging.rows_summary_ptr;
    let snapshot_len = staging.rows_snapshot.len();
    if live < 0x10000 {
        let deferred = ROW_RECORDS_RESTORE_DEFERRED.fetch_add(1, Ordering::SeqCst) + 1;
        if deferred <= 2 || deferred.is_power_of_two() {
            crate::host::append_autoload_debug(format_args!(
                "save-picker: deferred the staged-row restore for {reason} -- the live ProfileSummary is unreadable (0x{live:x}) #{deferred}; the snapshot stays armed and the next exit retries"
            ));
        }
        return false;
    }
    let writable =
        target >= 0x10000 && target == live && snapshot_len == PROFILE_SUMMARY_TOTAL_BYTES;
    if writable {
        // Safety: `target` is the allocation the snapshot was taken from, it is still the live
        // summary, and the image is exactly the size that was read out of it.
        unsafe {
            core::ptr::copy_nonoverlapping(
                staging.rows_snapshot.as_ptr(),
                target as *mut u8,
                snapshot_len,
            );
        }
    }
    staging.rows_staged = false;
    staging.rows_summary_ptr = 0;
    staging.rows_snapshot = Vec::new();
    drop(staging);
    ROW_RECORDS_RESTORED.fetch_add(1, Ordering::SeqCst);
    crate::host::append_autoload_debug(format_args!(
        "save-picker: restored the game's records over the staged browse rows for {reason} summary=0x{target:x} live=0x{live:x} bytes={snapshot_len} written={writable}"
    ));
    writable
}

/// Restores performed, and exits that found the summary unreadable and left the snapshot armed.
static ROW_RECORDS_RESTORED: AtomicUsize = AtomicUsize::new(0);
static ROW_RECORDS_RESTORE_DEFERRED: AtomicUsize = AtomicUsize::new(0);

/// How many restores have run, and how many were deferred.
pub fn row_records_restore_counts() -> (usize, usize) {
    (
        ROW_RECORDS_RESTORED.load(Ordering::SeqCst),
        ROW_RECORDS_RESTORE_DEFERRED.load(Ordering::SeqCst),
    )
}

/// `FUN_14067dc00` -- the character serializer, the one function that turns the live
/// `CS::ProfileSummary` into save bytes.
const SAVE_SERIALIZE_CHAR_RVA: u32 = 0x67dc00;

static SERIALIZE_GUARD_ORIG: AtomicUsize = AtomicUsize::new(0);
static SERIALIZE_GUARD_INSTALLED: AtomicUsize = AtomicUsize::new(0);
static SERIALIZE_GUARD_RESTORES: AtomicUsize = AtomicUsize::new(0);

/// Put the records back before the game serializes them, so the picker's rows cannot reach disk.
///
/// # Why the exit-path restores are not enough on their own
///
/// They run when the picker goes away. A save can fire while it is still open, and one did:
/// run br-20260913-021311-a481 staged five records, the player picked a destination, and the
/// forced save for that commit serialized the live records with the browse labels still in them.
/// The container came back with `[ new ]` and folder names in its slot list and no portraits, and
/// deleting saves does not help because the bytes on disk are the ones the game wrote.
///
/// This guard is the only one of the three that cannot be routed around, because every save goes
/// through this function. Re-entrancy is free: the latch is consumed, so the next call finds
/// nothing staged and forwards immediately.
///
/// # The cost, stated
///
/// `er-save-suppress` attributes its four save lanes by walking caller `rva`s, and this detour adds
/// a frame: its `save-dispatch attribution` reads `3/4` instead of `4/4` while this is installed.
/// That is an observer losing one label, against browse rows reaching the player's save.
///
/// # Safety
///
/// Installed by `er-hook` and called by the game on whichever thread is writing the save.
unsafe extern "system" fn save_serialize_row_guard_hook(
    a: usize,
    b: usize,
    c: usize,
    d: usize,
) -> usize {
    let orig = SERIALIZE_GUARD_ORIG.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    if rows_staged() {
        // Safety: the save thread, and the restore writes only the allocation it snapshotted.
        if unsafe { restore_row_records("save-serialize") } {
            SERIALIZE_GUARD_RESTORES.fetch_add(1, Ordering::SeqCst);
        }
    }
    // Safety: the union publishes either the game trampoline or the next handler; both accept
    // these four registers.
    let next: er_hook::UnionFn = unsafe { std::mem::transmute(orig) };
    unsafe { next(a, b, c, d) }
}

/// How many saves were made to write the game's own records rather than the picker's rows.
pub fn save_serialize_row_guard_restores() -> usize {
    SERIALIZE_GUARD_RESTORES.load(Ordering::SeqCst)
}

/// Install the serializer guard. Idempotent, and pass-through whenever nothing is staged.
///
/// # Safety
///
/// Process attach or startup-hook context.
pub unsafe fn install_save_serialize_row_guard() -> bool {
    if SERIALIZE_GUARD_INSTALLED
        .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return true;
    }
    let Ok(addr) = er_game_base::mem::game_rva_for_hook(SAVE_SERIALIZE_CHAR_RVA) else {
        crate::host::append_autoload_debug(format_args!(
            "save-picker: failed to resolve the character serializer rva 0x{SAVE_SERIALIZE_CHAR_RVA:x}; a save taken while the picker is open can write browse rows into the container"
        ));
        SERIALIZE_GUARD_INSTALLED.store(0, Ordering::SeqCst);
        return false;
    };
    match unsafe {
        er_hook::register_union_hook(addr, save_serialize_row_guard_hook, &SERIALIZE_GUARD_ORIG)
    } {
        Ok(()) => {
            crate::host::append_autoload_debug(format_args!(
                "save-picker: registered the character serializer 0x{addr:x} on the union; the game's own records go back before any save reads them"
            ));
            true
        }
        Err(status) => {
            crate::host::append_autoload_debug(format_args!(
                "save-picker: register_union_hook character serializer failed: {status:?}; a save taken while the picker is open can write browse rows into the container"
            ));
            SERIALIZE_GUARD_INSTALLED.store(0, Ordering::SeqCst);
            false
        }
    }
}
