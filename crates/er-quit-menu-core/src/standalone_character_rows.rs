//! The character-row content a standalone shell can decode for itself, so its `Load Character`
//! list looks like the product's instead of like the game's.
//!
//! # Why this exists
//!
//! [`crate::profile_row_chrome::RowPopulateHooks`] is the seam that dresses a character row, and
//! until now it had no caller anywhere in the workspace: `er-quickload` and `er-quit-rows` each
//! hook the row populate from their own private copy, so the seam sat filled with `None` and every
//! shell standing on this crate rendered bare rows under the derived movie's layout. The visible
//! result was the one measured on 2026-09-19 -- the mod's ten-row compacted list with the game's
//! own three-field text poured into it, no merged header and no attribute line.
//!
//! Turning the derived movie off would have removed the styling rather than fixed it. What was
//! missing was never the movie; it was the twenty lines of save decode behind it, and every piece
//! of that already exists in a shared crate:
//!
//! | piece | where it already lived |
//! |---|---|
//! | locate the live save directory | `er_title_flow`'s `SAVE_DIR_BUILDER_RVA` and its `u16string` offsets |
//! | decode slots | `er_save_loader::stats` / `bnd4` |
//! | compose `<name>, RL <n> WL <n>` | `er_loading_portrait_core::profile_row_label` |
//! | compose the attribute line | `er_loading_portrait_core::title_stats_text` |
//!
//! So this module is glue, not new reverse engineering. It reads the same file the game reads, by
//! the same native builder, and hands the same two strings to the same row chrome.
//!
//! # What it deliberately does not carry
//!
//! The product's `own_load` reader also honours a configured `save_file`, a staged private source,
//! a committed foreign pick and a terminal rejection latch. None of those exist in a shell with no
//! product behind it -- there is one save, the one the game itself loads -- so reproducing that
//! machinery here would be copying state this crate cannot have. The read is therefore the plain
//! case only, and it fails closed to [`CharacterRowFacts::UNKNOWN`], which renders the row exactly
//! as the game built it.
//!
//! The save file is opened read-only and never written, which is the rule this crate is already
//! held to for every user-provided save.

#[cfg(windows)]
use std::sync::atomic::{AtomicUsize, Ordering};

#[cfg(windows)]
use er_loading_portrait_core::profile_row_label::{RowHeaderValues, SHIPPED_ROW_HEADER_TEMPLATE};
#[cfg(windows)]
use er_title_flow::{
    SAVE_DIR_ALLOC_GETTER_RVA, SAVE_DIR_BUILDER_RVA, STEAM_ID_ACCESSOR_CALL_SLOT_RVA,
    U16STRING_ALLOC_OFFSET, U16STRING_CAP_OFFSET, U16STRING_DATA_OFFSET, U16STRING_SIZE_OFFSET,
    U16STRING_SSO_CAP,
};

#[cfg(windows)]
use crate::host::append_autoload_debug;
#[cfg(windows)]
use crate::profile_row_chrome::CharacterRowFacts;

/// Slots in a save container.
#[cfg(windows)]
const SLOT_COUNT: usize = 10;

/// Longest plausible save-directory path, in UTF-16 code units. The native builder writes an
/// `AppData` path; anything past this is a sign the string header was misread, and decoding it
/// would walk memory that is not a string.
#[cfg(windows)]
const SAVE_DIR_SANE_MAX_CODE_UNITS: usize = 320;

/// Save containers this shell will read, in preference order.
///
/// Asymmetric on purpose, matching the rule the product already follows: Seamless Co-op keeps its
/// characters in `ER0000.co2` and vanilla in `ER0000.sl2`, and a session running Seamless must
/// prefer the co-op container. Reading the wrong one puts another set of characters on the rows.
#[cfg(windows)]
const SAVE_FILE_NAMES: [&str; 2] = ["ER0000.co2", "ER0000.sl2"];

/// Plausible bounds for a save container, used only to reject an obviously wrong file before
/// spending a parse on it. Deliberately a range rather than the product's exact-size constant: that
/// constant tracks the container the product stages, and this reader takes the game's own file
/// whatever size the current version writes.
#[cfg(windows)]
const SAVE_BYTES_MIN: u64 = 1 << 20;
#[cfg(windows)]
const SAVE_BYTES_MAX: u64 = 64 << 20;

/// One slot's decoded row content, composed once and then handed out per populate.
#[cfg(windows)]
#[derive(Clone, Default)]
struct SlotRow {
    /// The merged `<name>, RL <n> WL <n>` header, already expanded.
    header: Option<String>,
    /// The compact attribute line, as Scaleform HTML in nul-terminated UTF-16.
    stats_html: Option<Vec<u16>>,
    /// The map this slot's body records, used to answer whether a `ProfileSummary` record that
    /// formats the row's `Location` really belongs to this character.
    saved_map: Option<i32>,
}

/// The decoded cache. `None` until the first row asks; a failed read stores an all-empty cache
/// rather than staying `None`, so the ~26 MB read is attempted once per process and not once per
/// row.
#[cfg(windows)]
static SLOT_ROWS: std::sync::OnceLock<Vec<SlotRow>> = std::sync::OnceLock::new();

#[cfg(windows)]
static ROWS_DRESSED: AtomicUsize = AtomicUsize::new(0);

/// Read the live save container's bytes: the exact file the game loads.
///
/// The directory comes from the native builder rather than a composed path, so the user-data root
/// and the Steam id are the engine's own answer and nothing here hardcodes either.
///
/// # Safety
///
/// Called on the menu thread with the game module loaded. Every read of the builder's output goes
/// through the fault-safe readers, and the builder is handed a stack buffer shaped like the
/// `u16string` wrapper it expects.
#[cfg(windows)]
unsafe fn read_live_save_bytes(base: usize) -> Option<Vec<u8>> {
    let alloc_getter: unsafe extern "system" fn() -> usize = unsafe {
        std::mem::transmute(crate::scaleform_proxy::gated_game_fn(
            SAVE_DIR_ALLOC_GETTER_RVA,
            "SAVE_DIR_ALLOC_GETTER_RVA",
        )?)
    };
    let allocator = unsafe { alloc_getter() };
    // Two frames down the builder calls through a qword in `.data`; a null there is a `CALL 0`, so
    // it is checked before the call rather than discovered inside it.
    let steam_id_call_slot = unsafe {
        er_game_base::mem::safe_read_usize(er_game_base::mem::game_data_addr(
            base,
            STEAM_ID_ACCESSOR_CALL_SLOT_RVA,
            "STEAM_ID_ACCESSOR_CALL_SLOT_RVA",
        ))
    }
    .unwrap_or(0);
    if allocator == 0 || steam_id_call_slot == 0 {
        append_autoload_debug(format_args!(
            "standalone-rows: save-dir build skipped allocator=0x{allocator:x} steam_id_call_slot=0x{steam_id_call_slot:x} (both must be non-null); rows stay native"
        ));
        return None;
    }
    // The MSVC stateful-allocator `u16string` wrapper the builder writes into: allocator at +0,
    // data at +0x08, size at +0x18, capacity at +0x20, with short strings stored inline.
    let mut wrapper = [0u64; 8];
    let wbase = wrapper.as_mut_ptr() as usize;
    unsafe {
        *((wbase + U16STRING_ALLOC_OFFSET) as *mut usize) = allocator;
        *((wbase + U16STRING_CAP_OFFSET) as *mut usize) = U16STRING_SSO_CAP;
    }
    let builder: unsafe extern "system" fn(usize) = unsafe {
        std::mem::transmute(crate::scaleform_proxy::gated_game_fn(
            SAVE_DIR_BUILDER_RVA,
            "SAVE_DIR_BUILDER_RVA",
        )?)
    };
    unsafe { builder(wbase) };
    let cap = unsafe { *((wbase + U16STRING_CAP_OFFSET) as *const usize) };
    let size = unsafe { *((wbase + U16STRING_SIZE_OFFSET) as *const usize) };
    let data = if cap >= 8 {
        unsafe { *((wbase + U16STRING_DATA_OFFSET) as *const usize) }
    } else {
        wbase + U16STRING_DATA_OFFSET
    };
    if data == 0 || size == 0 || size > SAVE_DIR_SANE_MAX_CODE_UNITS {
        append_autoload_debug(format_args!(
            "standalone-rows: save-dir builder returned nothing usable (cap={cap} size={size}); rows stay native"
        ));
        return None;
    }
    let mut dir = String::new();
    'decode: for unit in 0..size {
        let Some(code_unit) = (unsafe { er_game_base::mem::safe_read_u16(data + unit * 2) }) else {
            break;
        };
        if code_unit == 0 {
            break 'decode;
        }
        dir.push(char::from_u32(u32::from(code_unit)).unwrap_or('?'));
    }
    if dir.is_empty() {
        return None;
    }
    // The native path is a Windows one under Proton; std::fs wants forward slashes.
    let dir = std::path::PathBuf::from(dir.replace('\\', "/"));
    let path = SAVE_FILE_NAMES
        .iter()
        .map(|name| dir.join(name))
        .find(|path| {
            std::fs::metadata(path)
                .map(|meta| {
                    meta.is_file() && (SAVE_BYTES_MIN..=SAVE_BYTES_MAX).contains(&meta.len())
                })
                .unwrap_or(false)
        })?;
    match std::fs::read(&path) {
        Ok(bytes) => {
            append_autoload_debug(format_args!(
                "standalone-rows: read the game's own save container \"{}\" ({} bytes) read-only",
                path.display(),
                bytes.len()
            ));
            Some(bytes)
        }
        Err(err) => {
            append_autoload_debug(format_args!(
                "standalone-rows: could not read \"{}\" ({err}); rows stay native",
                path.display()
            ));
            None
        }
    }
}

/// Decode every slot into the row content it will render, once per process.
#[cfg(windows)]
fn slot_rows(base: usize) -> &'static [SlotRow] {
    SLOT_ROWS.get_or_init(|| {
        let Some(sl2) = (unsafe { read_live_save_bytes(base) }) else {
            return vec![SlotRow::default(); SLOT_COUNT];
        };
        let stats = er_save_loader::stats::all_slot_stats(&sl2);
        let mut names = er_save_loader::stats::all_slot_names(&sl2);
        // `all_slot_names` reads the name the stat block carries; the container's own active-slot
        // table names slots whose stat block did not decode, and a row with a name is better than a
        // row without one.
        for slot in er_save_loader::bnd4::active_character_slots(&sl2).unwrap_or_default() {
            if slot.slot < names.len() && names[slot.slot].is_none() {
                names[slot.slot] = Some(slot.name);
            }
        }
        let rows: Vec<SlotRow> = (0..SLOT_COUNT)
            .map(|slot| {
                let header = names[slot].clone().map(|name| {
                    let mut values = RowHeaderValues::from_name(name);
                    if let Some(decoded) = stats[slot].as_ref() {
                        values = values.with_rune_level(decoded.level);
                        if let Some(weapon_level) = decoded.matchmaking_weapon_level {
                            values = values.with_weapon_level(i32::from(weapon_level));
                        }
                    }
                    er_loading_portrait_core::profile_row_label::expand_row_header(
                        SHIPPED_ROW_HEADER_TEMPLATE,
                        &values,
                    )
                });
                let stats_html = stats[slot].as_ref().map(|decoded| {
                    er_loading_portrait_core::title_stats_text::build_title_stats_compact_html_utf16(
                        &decoded.attributes,
                    )
                });
                let saved_map = er_save_loader::bnd4::slot_body(&sl2, slot)
                    .ok()
                    .and_then(er_save_loader::bnd4::slot_saved_map);
                SlotRow {
                    header,
                    stats_html,
                    saved_map,
                }
            })
            .collect();
        let decoded = rows.iter().filter(|row| row.header.is_some()).count();
        append_autoload_debug(format_args!(
            "standalone-rows: decoded {decoded}/{SLOT_COUNT} character rows; first={:?}",
            rows.iter().find_map(|row| row.header.clone())
        ));
        rows
    })
}

/// What this shell knows about one character row.
///
/// Answers only about save slots. The transient current-player summary row shares slot index 0 with
/// save slot 0 and describes whoever is loaded rather than what slot 0 holds, so it is left native
/// rather than labelled with slot 0's character -- the same mistake the product's own comment
/// records having made.
///
/// # Safety
///
/// Called from inside the row populate that owns `row_model`.
#[cfg(windows)]
pub unsafe fn standalone_character_row_facts(
    base: usize,
    row_model: usize,
    slot: i32,
    is_current_player_row: bool,
) -> CharacterRowFacts {
    if is_current_player_row || slot < 0 || slot as usize >= SLOT_COUNT {
        return CharacterRowFacts::UNKNOWN;
    }
    let row = &slot_rows(base)[slot as usize];
    let Some(header) = row.header.as_ref() else {
        return CharacterRowFacts::UNKNOWN;
    };
    // The row's `Location` is formatted from a `ProfileSummary` record, and the records are
    // recycled independently of the bodies. The map is the one field both carry, so comparing them
    // is what keeps another character's place name off this row.
    let record_map = unsafe {
        er_game_base::mem::safe_read_i32(
            row_model
                + er_loading_portrait_core::profile_row_model::PROFILE_ROW_MODEL_SLOT_08_OFFSET,
        )
    };
    let location_available = match (row.saved_map, record_map) {
        (Some(body), Some(_record)) => body != 0,
        _ => false,
    };
    let dressed = ROWS_DRESSED.fetch_add(1, Ordering::SeqCst) + 1;
    if dressed <= 4 || dressed.is_power_of_two() {
        append_autoload_debug(format_args!(
            "standalone-rows: dressed slot={slot} header='{header}' stats={} location_available={location_available} (rows={dressed})",
            row.stats_html.is_some()
        ));
    }
    CharacterRowFacts {
        merged_header: Some(crate::scaleform_html::nul_terminated_utf16(header)),
        location_available,
        stats_html: row.stats_html.clone(),
    }
}
