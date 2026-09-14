//! The save picker's words: what each row, drive cell and status line reads, and the
//! Scaleform-HTML it says it in.
//!
//! A child module of `save_picker_menu` rather than a sibling, because every formatter here reads
//! the picker state its parent owns (`active_save_picker_lock`, the drive-strip focus, the path
//! editor). Rust privacy is by subtree, so the child sees those without any of them becoming
//! `pub(crate)` -- the extraction moves lines, not visibility.
//!
//! Split out of `save_picker_menu.rs` on 2026-09-14 under `scripts/check-rust-file-sizes.py`.

use super::*;

/// Escape text for the Scaleform-HTML SetText path (the `ErStats` row fields parse with bHTML=1,
/// so a character/file name containing `&`, `<` or `>` must not be interpreted as markup).
pub fn save_picker_html_escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            c => out.push(c),
        }
    }
    out
}

/// One dim Scaleform-HTML line for the browse rows' `ErStats` fields (same size/color language as
/// the stats panel's attribute lines), NUL-terminated UTF-16 for the native SetText wrapper. An
/// empty `text` yields a bare NUL so the field renders blank.
pub fn save_picker_browse_html_utf16(text: &str) -> Vec<u16> {
    save_picker_browse_html_utf16_color(text, "#8f887a")
}

pub fn save_picker_error_html_utf16(text: &str) -> Vec<u16> {
    save_picker_browse_html_utf16_color(text, "#d8a052")
}

pub fn save_picker_browse_html_utf16_color(text: &str, color: &str) -> Vec<u16> {
    // Match the native ProfileSelect filename/timestamp fields; the asset gives ErStats a native-height box.
    save_picker_html_utf16_color_size(text, color, 24)
}

fn save_picker_html_utf16_color_size(text: &str, color: &str, font_height: i32) -> Vec<u16> {
    if text.is_empty() {
        return vec![0];
    }
    let mut s = String::from("<p align=\"left\"><font size=\"");
    s.push_str(&font_height.to_string());
    s.push_str("\" color=\"");
    s.push_str(color);
    s.push_str("\">");
    s.push_str(&save_picker_html_escape(text));
    s.push_str("</font></p>");
    s.encode_utf16().chain(core::iter::once(0)).collect()
}

pub fn save_picker_set_visible_status(message: er_save_picker_core::PickerStatusMessage) {
    if let Some(model) = er_save_picker_core::model::active_save_picker_lock().as_mut() {
        model.set_status_message(message);
    }
}

/// Character budget for the per-file character list fragment. This text is merged onto the single
/// inline `ErStats` row field beside the filename and timestamp, so it must stay short enough to read
/// as row detail instead of a wrapped second line.
pub const SAVE_PICKER_BROWSE_LINE_CHAR_BUDGET: usize = 34;

/// Font height for one synthetic ProfileSelect field.
///
/// A host that ships the live-layout editor answers with whatever is authored right now. A host
/// that does not -- every standalone shell, and every unit test -- gets the shipped schema, which
/// is the same number `crates/er-gfx/profile_05_010_layout.toml` builds the GFX box from. The
/// absent hook must not answer 0: `size="0"` renders the drive strip and the path control
/// invisible, so a shell that installs no hooks would browse with unreadable chrome.
fn profile_editor_field_font_height(field_name: &str) -> i32 {
    match hooks().profile_editor_field_font_height {
        Some(height) => height(field_name),
        None => {
            er_gfx::profile_05_010_layout::shipped()
                .field(field_name)
                .font_height
        }
    }
}

pub fn save_picker_drive_cell_html_utf16(text: &str) -> Vec<u16> {
    // The button frame already supplies the visual boundary. Keep the model's `>C:<` / `[S:]`
    // wrappers for the boot overlay and selection semantics, but do not render that punctuation
    // inside the compact native button -- it clips before the drive letter does.
    let (selected, display) = if let Some(inner) = text
        .strip_prefix('>')
        .and_then(|inner| inner.strip_suffix('<'))
    {
        (true, inner)
    } else if let Some(inner) = text
        .strip_prefix('[')
        .and_then(|inner| inner.strip_suffix(']'))
    {
        (false, inner)
    } else {
        (false, text)
    };
    let color = if selected { "#d8a052" } else { "#8f887a" };
    let font_height = profile_editor_field_font_height("DriveCell_0");
    save_picker_html_utf16_color_size(display, color, font_height)
}

/// CurrentPath control colours: the parchment tone the rest of the picker chrome uses, and the
/// warning gold reserved for an entry the picker refused.
const SAVE_PICKER_PATH_NORMAL_COLOR: &str = "#b8b1a2";
const SAVE_PICKER_PATH_INVALID_COLOR: &str = "#e8c34a";

pub fn save_picker_current_path_text(row: usize) -> Option<Vec<u16>> {
    if save_picker_path_editor_active() {
        return Some(vec![0]);
    }
    if SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) == 0 && !missing_save_selection_pending() {
        return None;
    }
    let guard = er_save_picker_core::model::active_save_picker_lock();
    let model = guard.as_ref()?;
    if model.drive_row() != Some(row) {
        return Some(vec![0]);
    }
    // A rejected entry outranks the real folder: the user sees exactly what they typed, marked
    // invalid, until they correct it (any successful commit or navigation refreshes the listing,
    // which drops the rejected text and returns this control to the normal colour).
    let (text, color) = match model.rejected_path_text() {
        Some(rejected) => (rejected, SAVE_PICKER_PATH_INVALID_COLOR),
        None => (model.current_dir().to_str()?, SAVE_PICKER_PATH_NORMAL_COLOR),
    };
    let escaped = save_picker_html_escape(text);
    let font_height = profile_editor_field_font_height("CurrentPath");
    let html = format!(
        "<p align=\"left\"><font size=\"{font_height}\" color=\"{color}\">{escaped}</font></p>"
    );
    Some(html.encode_utf16().chain(core::iter::once(0)).collect())
}

pub fn save_picker_drive_cell_text(row: usize, cell: usize) -> Option<Vec<u16>> {
    if SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) == 0 && !missing_save_selection_pending() {
        return None;
    }
    let guard = er_save_picker_core::model::active_save_picker_lock();
    let model = guard.as_ref()?;
    let text = model.drive_row_cell_label(row, cell).unwrap_or_default();
    Some(save_picker_drive_cell_html_utf16(&text))
}

/// The `ErStats` fragments for ProfileSelect row `row` while the browse picker owns the window.
/// The row-populate hook merges the two fragments into one inline field: file rows show active-slot
/// count plus character names/levels beside `ER0000.sl2`, while navigation/status rows show their
/// auxiliary copy beside the row label. Empty rows get blank fragments so neither leftover row text
/// nor per-slot attribute stats render as junk there. `None` when the picker does not own the rows
/// (the normal character-slot view keeps the attribute stats panel).
pub fn save_picker_browse_stats_lines(row: usize) -> Option<(Vec<u16>, Vec<u16>)> {
    if SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) == 0 && !missing_save_selection_pending() {
        return None;
    }
    let guard = er_save_picker_core::model::active_save_picker_lock();
    let model = guard.as_ref()?;
    let status_row = model.status_message().is_some() && row == 0;
    if let Some((top, bottom)) = model.row_auxiliary_lines(row) {
        if status_row {
            return Some((
                save_picker_error_html_utf16(&top),
                save_picker_error_html_utf16(&bottom),
            ));
        }
        return Some((
            save_picker_browse_html_utf16(&top),
            save_picker_browse_html_utf16(&bottom),
        ));
    }
    let is_current = model.row_is_loaded_save(row);
    let Some(chars) = model.row_file_characters(row) else {
        // Empty row: blank the injected stats field so no per-slot attribute stats render as junk.
        return Some((vec![0], vec![0]));
    };
    let count = if chars.len() == 1 {
        "1 CHAR".to_owned()
    } else {
        format!("{} CHAR", chars.len())
    };
    let top = if is_current {
        format!("* {count}")
    } else {
        count
    };
    let mut bottom = String::new();
    let mut shown = 0usize;
    for info in chars {
        let seg = format!("{} L{}", info.name, info.level);
        let sep = if bottom.is_empty() { "" } else { " / " };
        if !bottom.is_empty()
            && bottom.chars().count() + sep.chars().count() + seg.chars().count()
                > SAVE_PICKER_BROWSE_LINE_CHAR_BUDGET
        {
            break;
        }
        bottom.push_str(sep);
        bottom.push_str(&seg);
        shown += 1;
    }
    if shown < chars.len() {
        bottom.push_str(&format!(" +{}", chars.len() - shown));
    }
    Some((
        save_picker_browse_html_utf16(&top),
        save_picker_browse_html_utf16(&bottom),
    ))
}

/// What a picker-owned row does with every optional ProfileSelect field family.
///
/// The `Level` caption/value and bottom `PlayTime` are hidden for every picker row. The remaining
/// fields are row-kind-specific: a save-file row can stage its timestamp into top-right `Location`,
/// metadata rows own `ErStats`, and only the drive-cycle row owns populated `DriveCell_0..25` cells.
pub struct RowSlotInfo {
    /// Replacement text for the `Location` field (when the file was last written), or `None` to hide
    /// the field -- which is what every non-file row gets, and what a file whose timestamp is
    /// unreadable gets rather than a fabricated date.
    pub location: Option<String>,
    /// Whether this row has real `ErStats` copy. False on the drive row unless a visible status
    /// message temporarily owns it, so stale parent-folder copy cannot survive row-clip reuse.
    pub er_stats: bool,
    /// Number of populated drive-strip cells on this row. Zero outside the drive row and while a
    /// visible status message temporarily owns its field band.
    pub drive_cell_count: usize,
    /// Focus target whose geometry the row's native animated Cursor must follow.
    pub drive_strip_focus: Option<er_save_picker_core::DriveStripFocus>,
}

/// What the browse picker wants done with ProfileSelect row `row`'s per-slot info fields.
///
/// `None` when the picker does not own the rows. That is the load-bearing half of the scope: the
/// vanilla character-slot views, the title-screen Load Game list first among them, render from the
/// game's own records and must be left exactly as the game draws them. Same ownership gate as
/// [`save_picker_browse_stats_lines`], so the two cannot disagree about who owns a row.
pub fn save_picker_row_slot_info(row: usize) -> Option<RowSlotInfo> {
    if SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) == 0 && !missing_save_selection_pending() {
        return None;
    }
    let (last_saved, er_stats, drive_cell_count, drive_strip_focus) = {
        let guard = er_save_picker_core::model::active_save_picker_lock();
        let model = guard.as_ref()?;
        let has_auxiliary_lines = model.row_auxiliary_lines(row).is_some();
        let drive_cell_count = if model.drive_row() == Some(row) && !has_auxiliary_lines {
            model.drive_strip_cell_count()
        } else {
            0
        };
        let drive_strip_focus = (drive_cell_count > 0)
            .then(|| model.drive_strip_focus())
            .flatten();
        (
            model.row_last_saved(row),
            has_auxiliary_lines || model.row_file_characters(row).is_some(),
            drive_cell_count,
            drive_strip_focus,
        )
    };
    Some(RowSlotInfo {
        location: last_saved.and_then(save_picker_last_saved_text),
        er_stats,
        drive_cell_count,
        drive_strip_focus,
    })
}

/// Render one file's modification time as the row's last-saved text, in local time.
/// `None` when the stamp predates the epoch or the OS cannot give a local offset for it -- the row
/// then hides the field rather than showing a date we cannot stand behind.
pub fn save_picker_last_saved_text(modified: std::time::SystemTime) -> Option<String> {
    let secs = modified
        .duration_since(std::time::SystemTime::UNIX_EPOCH)
        .ok()?
        .as_secs();
    let secs = i64::try_from(secs).ok()?;
    er_save_picker_core::model::format_last_saved(secs, unsafe { local_utc_offset_seconds(secs) }?)
}

/// The local zone's offset from UTC at the instant `utc_secs`, in seconds.
///
/// Asks Windows rather than assuming, and asks about that instant rather than about now:
/// `SystemTimeToTzSpecificLocalTime` applies the zone's DST rules for the given date, so a save
/// written on the other side of a DST boundary still renders the wall-clock time it was written at.
/// (Comparing `GetLocalTime` to `GetSystemTime` would give only the current offset and misdate every
/// file from the other side of the boundary by an hour.) The offset comes back as a number, which is
/// all the pure formatter needs -- that is what keeps the rendering unit-testable.
/// # Safety
///
/// Calls the Win32 time-zone API, which has no precondition beyond being on Windows. It is `unsafe`
/// because that call is, not because a caller can get it wrong.
pub unsafe fn local_utc_offset_seconds(utc_secs: i64) -> Option<i64> {
    use windows::Win32::Foundation::{FILETIME, SYSTEMTIME};
    use windows::Win32::System::Time::{
        FileTimeToSystemTime, SystemTimeToFileTime, SystemTimeToTzSpecificLocalTime,
    };

    /// 100ns ticks per second, and the seconds between the FILETIME (1601) and Unix (1970) epochs.
    const TICKS_PER_SECOND: i64 = 10_000_000;
    const FILETIME_EPOCH_TO_UNIX_SECONDS: i64 = 11_644_473_600;

    fn to_filetime(secs: i64) -> Option<FILETIME> {
        let ticks = secs
            .checked_add(FILETIME_EPOCH_TO_UNIX_SECONDS)?
            .checked_mul(TICKS_PER_SECOND)
            .and_then(|t| u64::try_from(t).ok())?;
        Some(FILETIME {
            dwLowDateTime: ticks as u32,
            dwHighDateTime: (ticks >> 32) as u32,
        })
    }

    let utc_ft = to_filetime(utc_secs)?;
    let mut utc_st = SYSTEMTIME::default();
    unsafe { FileTimeToSystemTime(&utc_ft, &mut utc_st) }.ok()?;
    let mut local_st = SYSTEMTIME::default();
    unsafe { SystemTimeToTzSpecificLocalTime(None, &utc_st, &mut local_st) }.ok()?;
    // Reading the local wall clock back as if it were UTC turns it into "unix seconds shifted by the
    // offset", so the difference is the offset the zone applied at that instant.
    let mut local_ft = FILETIME::default();
    unsafe { SystemTimeToFileTime(&local_st, &mut local_ft) }.ok()?;
    let local_ticks =
        (u64::from(local_ft.dwHighDateTime) << 32) | u64::from(local_ft.dwLowDateTime);
    let local_secs =
        i64::try_from(local_ticks / TICKS_PER_SECOND as u64).ok()? - FILETIME_EPOCH_TO_UNIX_SECONDS;
    Some(local_secs - utc_secs)
}

#[cfg(test)]
mod save_picker_row_slot_info_tests {
    use super::*;
    use std::sync::atomic::Ordering;

    /// Scope proof for the row Level/PlayTime rework: with no picker owning the rows -- the state
    /// the vanilla character-slot views run in, the title-screen Load Game list among them -- the
    /// gate answers `None` for every row, and `None` is the only answer the populate hook treats as
    /// "leave this row exactly as the game drew it". A regression that made the suppression or the
    /// last-saved text global would have to make this return `Some` here first.
    #[test]
    fn no_picker_means_no_row_is_ever_classified() {
        assert_eq!(
            SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst),
            0,
            "no picker session may be active in a unit test"
        );
        for row in 0..er_save_picker_core::model::PICKER_ROW_COUNT {
            assert!(
                save_picker_row_slot_info(row).is_none(),
                "row {row} was classified without a picker owning the rows"
            );
            assert!(
                save_picker_browse_stats_lines(row).is_none(),
                "row {row} got browse stats without a picker owning the rows"
            );
        }
    }
}
