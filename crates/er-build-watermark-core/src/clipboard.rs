//! The Windows clipboard, for imgui text fields in every overlay panel.
//!
//! imgui-sys is built with `IMGUI_DISABLE_WIN32_FUNCTIONS`, so the shared context starts with no
//! clipboard at all: Ctrl+C and Ctrl+V in an overlay field only ever reached imgui's own
//! in-process buffer. Only the host holds the `&mut Context` a backend is installed on, so the
//! overlay host installs this once and every guest's fields get the real clipboard through it.

use windows::Win32::Foundation::{GlobalFree, HANDLE, HGLOBAL};
use windows::Win32::System::DataExchange::{
    CloseClipboard, EmptyClipboard, GetClipboardData, OpenClipboard, SetClipboardData,
};
use windows::Win32::System::Memory::{GMEM_MOVEABLE, GlobalAlloc, GlobalLock, GlobalUnlock};

/// `CF_UNICODETEXT`: a NUL-terminated UTF-16 string. Spelled here rather than pulling in the Ole
/// feature for one constant.
const CF_UNICODETEXT: u32 = 13;

/// imgui's clipboard, backed by the system one. Owned by the shared context once installed.
pub struct Win32Clipboard;

/// Opened for the life of the value and closed on drop, so an early return cannot leave the
/// clipboard locked against every other program.
struct OpenGuard;

impl OpenGuard {
    fn open() -> Option<Self> {
        // SAFETY: no owner window; the clipboard is released in `drop`.
        unsafe { OpenClipboard(None) }.ok().map(|()| OpenGuard)
    }
}

impl Drop for OpenGuard {
    fn drop(&mut self) {
        // SAFETY: this guard is the open call's pair.
        let _ = unsafe { CloseClipboard() };
    }
}

impl hudhook::imgui::ClipboardBackend for Win32Clipboard {
    fn get(&mut self) -> Option<String> {
        let _open = OpenGuard::open()?;
        // SAFETY: the clipboard is open; the handle stays owned by the clipboard.
        let handle = unsafe { GetClipboardData(CF_UNICODETEXT) }.ok()?;
        let memory = HGLOBAL(handle.0);
        // SAFETY: CF_UNICODETEXT data is a global memory block holding a NUL-terminated string.
        let text = unsafe { GlobalLock(memory) }.cast::<u16>();
        if text.is_null() {
            return None;
        }
        let mut len = 0;
        // SAFETY: the block is terminated by a NUL code unit, read only up to it.
        while unsafe { *text.add(len) } != 0 {
            len += 1;
        }
        // SAFETY: `len` code units were just read from the locked block.
        let units = unsafe { std::slice::from_raw_parts(text, len) };
        let value = String::from_utf16(units).ok();
        // SAFETY: paired with the lock above.
        let _ = unsafe { GlobalUnlock(memory) };
        value
    }

    fn set(&mut self, value: &str) {
        let units: Vec<u16> = value.encode_utf16().chain(std::iter::once(0)).collect();
        let Some(_open) = OpenGuard::open() else {
            return;
        };
        // SAFETY: the clipboard is open.
        if unsafe { EmptyClipboard() }.is_err() {
            return;
        }
        let bytes = units.len() * size_of::<u16>();
        // SAFETY: a fresh moveable block, filled and handed to the clipboard below.
        let Ok(memory) = (unsafe { GlobalAlloc(GMEM_MOVEABLE, bytes) }) else {
            return;
        };
        // SAFETY: `memory` was just allocated with room for `units`.
        let target = unsafe { GlobalLock(memory) }.cast::<u16>();
        if target.is_null() {
            // SAFETY: never handed to the clipboard, so still ours to free.
            let _ = unsafe { GlobalFree(Some(memory)) };
            return;
        }
        // SAFETY: both ranges are `units.len()` code units long and do not overlap.
        unsafe { std::ptr::copy_nonoverlapping(units.as_ptr(), target, units.len()) };
        // SAFETY: paired with the lock above.
        let _ = unsafe { GlobalUnlock(memory) };
        // SAFETY: on success the clipboard owns `memory`; on failure it is still ours.
        if unsafe { SetClipboardData(CF_UNICODETEXT, Some(HANDLE(memory.0))) }.is_err() {
            // SAFETY: the clipboard refused it, so it was never taken over.
            let _ = unsafe { GlobalFree(Some(memory)) };
        }
    }
}
