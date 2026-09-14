//! The `CreateFileW` write-open detour the destination commit's redirect window is read by.
//!
//! # Why the armed window was not enough
//!
//! `save_dest_arm_redirect` snapshots the loaded save, seeds the destination with those bytes and
//! arms a window; `save_dest_redirect_for_open` then answers, for one incoming write-open, whether
//! it is the loaded save's own container and what to open instead. Nothing in a standalone shell
//! ever asked it that question: the only caller was `er-quickload`'s save-redirect path hook, so
//! the native writer opened the loaded save, wrote the player's save into it, and the commit's own
//! verification reported the truth afterwards: `changed_from_seed=false`, and a line reading "live
//! save mutated during a destination commit -- the redirect leaked" (br-20260913-000002-47f1). The
//! pre-fire snapshot was restored over the live save, so that run cost nothing but the save the
//! player asked for.
//!
//! # What this diverts
//!
//! Exactly what `save_dest_redirect_for_open` returns a destination for, which is the loaded save's
//! own container matched by full path, and only while a commit window is armed. Read-opens and the
//! `.bak` copy pass through by that function's own rule, and every other path in the process is
//! handed to the original API untouched.
//!
//! The product's detour additionally observes Steam ids, stages direct-file trees and records
//! save-like path diagnostics. None of that belongs to a row that writes one file to one place, so
//! none of it is here.

use std::cell::Cell;
use std::ffi::c_void;
use std::sync::atomic::Ordering;

use er_save_redirect::{
    SAVE_HOOK_ORIGINAL_UNSET, SAVE_REDIRECT_ORIG_CREATEFILEW, SaveHookInstallState,
    install_core_createfilew_hook,
};

use crate::host::append_autoload_debug;
use crate::save_dest_commit_runtime::{save_dest_note_redirect_hit, save_dest_redirect_for_open};

/// Win32 `CreateFileW`, as the detour and the trampoline both see it.
type CreateFileWFn =
    unsafe extern "system" fn(*const u16, u32, u32, isize, u32, u32, isize) -> isize;

/// `INVALID_HANDLE_VALUE`, which is what a failed open returns.
const INVALID_HANDLE: isize = -1;

static INSTALL_STATE: SaveHookInstallState = SaveHookInstallState::new();

thread_local! {
    /// Detour depth on this thread. The body opens files of its own -- the debug log, and the
    /// directory-identity probe inside the resolver -- and each of those re-enters here. A nested
    /// entry is our own open of a path we already decided on, so it wants the original API and
    /// none of the decision.
    static DETOUR_DEPTH: Cell<usize> = const { Cell::new(0) };
}

/// Length of a NUL-terminated wide string, bounded so a non-terminated buffer cannot walk the heap.
///
/// # Safety
/// `text` must be a valid pointer to a NUL-terminated UTF-16 string, or null.
unsafe fn wide_len(text: *const u16) -> usize {
    if text.is_null() {
        return 0;
    }
    // Win32 paths are capped at 32,767 units even in their extended form.
    const MAX_UNITS: usize = 0x8000;
    let mut len = 0usize;
    while len < MAX_UNITS {
        if unsafe { *text.add(len) } == 0 {
            break;
        }
        len += 1;
    }
    len
}

unsafe extern "system" fn save_dest_create_file_w_hook(
    file_name: *const u16,
    access: u32,
    share: u32,
    security: isize,
    disposition: u32,
    flags: u32,
    template: isize,
) -> isize {
    let orig = SAVE_REDIRECT_ORIG_CREATEFILEW.load(Ordering::SeqCst);
    if orig == SAVE_HOOK_ORIGINAL_UNSET {
        return INVALID_HANDLE;
    }
    let call: CreateFileWFn = unsafe { std::mem::transmute::<usize, CreateFileWFn>(orig) };
    let pass_through = || unsafe {
        call(
            file_name,
            access,
            share,
            security,
            disposition,
            flags,
            template,
        )
    };

    let nested = DETOUR_DEPTH.with(|depth| {
        let entered = depth.get();
        depth.set(entered + 1);
        entered != 0
    });
    if nested {
        DETOUR_DEPTH.with(|depth| depth.set(depth.get() - 1));
        return pass_through();
    }

    let len = unsafe { wide_len(file_name) };
    let destination = (len != 0).then(|| {
        // Safety: `wide_len` walked this many units of the caller's buffer without reaching its end.
        let path = unsafe { std::slice::from_raw_parts(file_name, len) };
        save_dest_redirect_for_open(path, access)
    });

    let result = match destination.flatten() {
        Some(destination) => {
            let ret = unsafe {
                call(
                    destination.as_ptr(),
                    access,
                    share,
                    security,
                    disposition,
                    flags,
                    template,
                )
            };
            save_dest_note_redirect_hit(ret != INVALID_HANDLE);
            ret
        }
        None => pass_through(),
    };
    DETOUR_DEPTH.with(|depth| depth.set(depth.get().saturating_sub(1)));
    result
}

/// Resolve an export of `kernel32.dll`, or [`SAVE_HOOK_ORIGINAL_UNSET`] when it cannot be found.
fn kernel32_export(name: &[u8]) -> usize {
    use windows::Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress};
    use windows::core::{PCSTR, s};

    let Ok(module) = (unsafe { GetModuleHandleA(s!("kernel32.dll")) }) else {
        return SAVE_HOOK_ORIGINAL_UNSET;
    };
    // The caller passes its own NUL-terminated byte string, which is exactly what `PCSTR` wants.
    match unsafe { GetProcAddress(module, PCSTR(name.as_ptr())) } {
        Some(address) => address as usize,
        None => SAVE_HOOK_ORIGINAL_UNSET,
    }
}

/// Install the write-open detour once, so an armed destination commit is actually consulted.
///
/// Idempotent and cheap to call from the flow's own install step. The detour is pass-through until
/// a commit arms the redirect window, so installing it early costs one branch per file open and
/// changes nothing else about the process.
pub fn install_save_dest_open_redirect() {
    unsafe {
        install_core_createfilew_hook(
            &INSTALL_STATE,
            save_dest_create_file_w_hook as *mut c_void,
            kernel32_export,
            |message| append_autoload_debug(format_args!("{message}")),
        );
    }
}

/// Whether the detour is live. False means an armed redirect would be read by nobody, which is the
/// leak this module exists to close.
///
/// The question is about the process, not about this module. `er-quickload`'s save-redirect path
/// hook detours the same `kernel32!CreateFileW` and its body asks `save_dest_redirect_for_open` the
/// same question, so when the product holds the detour an armed window is read and this must say
/// so. Asking `INSTALL_STATE` alone said no, and the commit refused a save the redirect would have
/// carried -- see `er_save_redirect::core_createfilew_installed_in_process`, which carries the
/// measurement.
pub fn save_dest_open_redirect_installed() -> bool {
    INSTALL_STATE.core_createfilew_installed()
        || er_save_redirect::core_createfilew_installed_in_process()
}
