//! Rebuild EVERY live `CS::ProfileSummary` record from one save container's own bytes.
//!
//! Moved from er-quickload `experiments/startup_hooks/quit_menu/save_swap_profile_table.rs`, whose
//! remaining half (the System>Quit preview's snapshot/backout bookkeeping and the renderer refresh)
//! stayed behind with the menu it belongs to. This is the transport both callers share: the
//! System>Quit foreign-save preview, and the boot autoload's re-read of a picked container.

use core::sync::atomic::Ordering;

use er_game_base::profile_summary::{
    PROFILE_SUMMARY_ACTIVE_FLAGS_OFFSET, PROFILE_SUMMARY_RECORD_STRIDE,
    PROFILE_SUMMARY_SLOT_COUNT as TITLE_PROFILE_SLOT_COUNT, profile_summary_record_address,
    profile_summary_record_offset,
};

use crate::host::append_autoload_debug;
use crate::serialized_slot::{
    PROFILE_PREVIEW_FACE_HASH, PROFILE_PREVIEW_PLACE_NAME_UNSOURCED, SerializedSaveSlot,
};

/// Rebuild EVERY live `CS::ProfileSummary` record from one save container's own bytes.
///
/// Pure record transport: it zeroes the ten records + occupancy bytes, then rewrites one record per
/// slot the container's `USER_DATA010` occupancy bitmap marks active, from that slot's own body --
/// name, level, play time, rune memory, map, `PlaceName`, `FaceData` and `ChrAsm`. It does NO
/// snapshot/backout bookkeeping and NO renderer refresh: the callers own those, because the two
/// callers want opposite things from them (a System>Quit preview is reversible; a boot autoload's
/// re-read is not a preview at all).
///
/// `summary_snapshot` is the whole `CS::ProfileSummary` allocation as it looked BEFORE this call --
/// used only as a STRUCTURAL template for slots whose visual blocks cannot be located, and read
/// before the zeroing below, so callers must capture it first.
///
/// Returns the mask of slots written plus each written slot's attribute line.
/// # Safety
///
/// `summary` must be the LIVE `CS::ProfileSummary` allocation: this zeroes all ten records and
/// their occupancy bytes through raw pointers before rewriting them, with no fault guard, and
/// `summary_snapshot` must be the `PROFILE_SUMMARY_TOTAL_BYTES` image of that same allocation as
/// it looked BEFORE the call (see above). `base` must be the running game module base.
///
/// Must run on the game thread. It is destructive and NOT reversible by itself -- a caller that
/// needs to back out owns the snapshot/restore, which is why this function does none.
pub unsafe fn write_profile_summary_records_from_save_bytes(
    base: usize,
    summary: usize,
    summary_snapshot: &[u8],
    bytes: &[u8],
) -> (usize, Vec<Vec<u16>>) {
    let fallback_slot = (0..TITLE_PROFILE_SLOT_COUNT).find(|slot| {
        summary_snapshot
            .get(PROFILE_SUMMARY_ACTIVE_FLAGS_OFFSET + *slot)
            .copied()
            .unwrap_or(0)
            != 0
    });
    unsafe {
        for (slot, face_hash) in PROFILE_PREVIEW_FACE_HASH
            .iter()
            .enumerate()
            .take(TITLE_PROFILE_SLOT_COUNT)
        {
            let record = profile_summary_record_address(summary, slot);
            core::ptr::write_bytes(record as *mut u8, 0, PROFILE_SUMMARY_RECORD_STRIDE);
            *((summary + PROFILE_SUMMARY_ACTIVE_FLAGS_OFFSET + slot) as *mut u8) = 0;
            face_hash.store(0, Ordering::SeqCst);
        }
    }
    PROFILE_PREVIEW_PLACE_NAME_UNSOURCED.store(0, Ordering::SeqCst);

    let mut preview_stats = vec![Vec::new(); TITLE_PROFILE_SLOT_COUNT];
    let Ok(active_slots) = er_save_loader::bnd4::active_slots(bytes) else {
        append_autoload_debug(format_args!(
            "system-quit-load-save-profiles: replacement preview refused -- active-slot bitmap unreadable"
        ));
        return (0, preview_stats);
    };
    let mut mask = 0usize;
    for (slot, slot_stats) in preview_stats.iter_mut().enumerate() {
        if !active_slots.get(slot).copied().unwrap_or(false) {
            continue;
        }
        if let Ok(body) = er_save_loader::bnd4::slot_body(bytes, slot) {
            let slot_body = SerializedSaveSlot::new(body);
            let Some(pgd) = slot_body.player_game_data() else {
                continue;
            };
            let Some(saved_map) = slot_body.saved_map() else {
                continue;
            };
            let fallback_src_slot = if summary_snapshot
                .get(PROFILE_SUMMARY_ACTIVE_FLAGS_OFFSET + slot)
                .copied()
                .unwrap_or(0)
                != 0
            {
                Some(slot)
            } else {
                fallback_slot
            };
            let fallback = fallback_src_slot.and_then(|src_slot| {
                let start = profile_summary_record_offset(src_slot);
                summary_snapshot.get(start..start + PROFILE_SUMMARY_RECORD_STRIDE)
            });
            let playtime_ticks = slot_body.in_game_timer_ticks(pgd).unwrap_or(0);
            // The place name is NOT in the character body -- the game writes it from the front-end
            // manager at save time. It IS in the save's own stored summary table, so take it from
            // there rather than deriving one from the map id.
            let place_name_id = er_save_loader::profile_summary::slot_place_name_id(bytes, slot);
            let face_bytes = slot_body.face_data_buffer_bytes(pgd);
            let chr_asm_image = slot_body.runtime_chr_asm_image(pgd);
            if unsafe {
                pgd.write_profile_summary_record(
                    base,
                    summary,
                    slot,
                    saved_map,
                    place_name_id,
                    playtime_ticks,
                    fallback,
                    face_bytes,
                    chr_asm_image.as_ref(),
                )
            } {
                append_autoload_debug(format_args!(
                    "system-quit-load-save-profiles: preview slot {slot} playtime_ticks={playtime_ticks}"
                ));
                if let Some(stats) = pgd.stats_text_utf16() {
                    *slot_stats = stats;
                }
                mask |= 1usize << slot;
            }
        }
    }
    (mask, preview_stats)
}
