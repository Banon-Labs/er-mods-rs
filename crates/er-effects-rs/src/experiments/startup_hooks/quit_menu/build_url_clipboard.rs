//! Reading the Windows clipboard, so the build-url editor can open pre-filled with a copied link.
//!
//! # Why this exists at all
//!
//! The obvious design is "open a text field and let the player paste". That does not work: Elden
//! Ring's native `CS::SoftwareKeyboard` cannot paste. The game's import table links
//! `OpenClipboard`, `GetClipboardData` and `SetClipboardData`, but a byte scan of the whole 98 MB
//! image for `call`/`jmp`/`mov` against all three IAT slots (`0x1429af920`, `0x1429af930`,
//! `0x1429af940`) finds ZERO references -- they are linked by the CRT and never called. Ctrl+V in
//! that field does nothing, and no amount of work on our side changes that.
//!
//! So the paste happens on OUR side of the boundary, before the field opens: this DLL reads the
//! clipboard and hands the text to the keyboard as its initial value. The player copies a link in
//! their browser, presses the row, and the link is already there.
//!
//! Under Wine the Windows clipboard is bridged to the host X11/Wayland selection, so a link copied
//! in a Linux browser arrives here. That bridge is the one part of this that cannot be proven
//! statically; a failure to read simply falls back to the bare prefix, which is the same experience
//! as not having a link copied.

use super::*;

use windows::Win32::Foundation::{HANDLE, HWND};
use windows::Win32::System::DataExchange::{
    CloseClipboard, GetClipboardData, IsClipboardFormatAvailable, OpenClipboard,
};
use windows::Win32::System::Memory::{GlobalLock, GlobalUnlock};

/// `CF_UNICODETEXT`. The only format asked for: the field is UTF-16 and a link is text.
const CF_UNICODETEXT: u32 = 13;

/// Longest clipboard text considered. A share link is ~60 characters; anything past this is not a
/// link the player meant to paste, and the bound keeps a huge clipboard (a copied document) from
/// being walked a code unit at a time.
const MAX_CLIPBOARD_UNITS: usize = 512;

/// The clipboard's text, or `None` when there is none, it is not text, or the clipboard is locked
/// by another process.
///
/// Every failure is `None` rather than an error: the caller's response to all of them is the same
/// (open with the bare prefix instead), and a player whose clipboard holds an image does not need
/// to be told about it.
pub(crate) fn clipboard_text() -> Option<String> {
    // Safety: the clipboard API is process-wide and this is the only place this DLL touches it.
    // Every handle is released on every path below, including the early returns.
    unsafe {
        if IsClipboardFormatAvailable(CF_UNICODETEXT).is_err() {
            return None;
        }
        // A null owner is legal and means "this task". The clipboard is briefly locked to every
        // other process while open, so it is closed again as soon as the bytes are copied out.
        if OpenClipboard(Some(HWND::default())).is_err() {
            return None;
        }
        let text = read_open_clipboard();
        let _ = CloseClipboard();
        text
    }
}

/// Copy the open clipboard's UTF-16 text out. Split from [`clipboard_text`] so there is exactly one
/// `CloseClipboard`, on every path, no matter which read step fails.
///
/// # Safety
///
/// The clipboard must be open and `CF_UNICODETEXT` available.
unsafe fn read_open_clipboard() -> Option<String> {
    // Safety: guaranteed by the caller's contract.
    let handle: HANDLE = unsafe { GetClipboardData(CF_UNICODETEXT) }.ok()?;
    if handle.is_invalid() {
        return None;
    }
    // Safety: a CF_UNICODETEXT handle is a movable global; locking yields the wide string.
    let locked = unsafe { GlobalLock(windows::Win32::Foundation::HGLOBAL(handle.0)) };
    if locked.is_null() {
        return None;
    }
    let mut units: Vec<u16> = Vec::new();
    for index in 0..MAX_CLIPBOARD_UNITS {
        // Safety: the block is NUL-terminated by the CF_UNICODETEXT contract, and the loop is
        // bounded independently so a clipboard that lies cannot walk off the allocation.
        let unit = unsafe { *(locked as *const u16).add(index) };
        if unit == 0 {
            break;
        }
        units.push(unit);
    }
    // Safety: paired with the lock above.
    let _ = unsafe { GlobalUnlock(windows::Win32::Foundation::HGLOBAL(handle.0)) };
    String::from_utf16(&units).ok()
}

/// The text the editor should open with: the clipboard when it holds an importable link, else the
/// bare prefix for the player to complete.
///
/// The clipboard is only used when it VALIDATES. Prefilling with arbitrary clipboard contents would
/// mean a player who last copied a paragraph opens the field holding a paragraph, and has to clear
/// it before they can type -- strictly worse than the prefix.
pub(crate) fn build_url_initial_text() -> String {
    match clipboard_text() {
        Some(text) if er_build_import::validate_build_url(&text).is_ok() => {
            let trimmed = text.trim().to_owned();
            append_autoload_debug(format_args!(
                "system-quit-build-url: prefilling the editor from the clipboard ({} chars)",
                trimmed.chars().count()
            ));
            trimmed
        }
        _ => er_build_import::BUILD_URL_PREFIX.to_owned(),
    }
}
