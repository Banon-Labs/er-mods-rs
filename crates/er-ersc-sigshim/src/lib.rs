//! Signature shim that lets Seamless Co-op v1.9.9 boot on ELDEN RING 1.17.0.0.
//!
//! # The failure this fixes
//!
//! On 1.17 `ersc.dll` aborts during its own init with a modal box titled
//! `Seamless Coop 1.9.9 - Fatal Error`:
//!
//! ```text
//! No such pattern
//!
//! "E8 ? ? ? ? 48 8B 15 ? ? ? ? 48 8D 4B 20"
//!
//! The mod may not be compatible with the installed game version
//! ersc\signatures\signatures.cpp
//! 1399
//! ```
//!
//! The game process then sits at 0 CPU behind that modal box with no window of its own, which
//! is what a user sees as "Seamless hangs on the new patch".
//!
//! # Why the pattern stopped matching
//!
//! It is a *locator*, not a hook site. It matches one call site inside `GetWwiseSettings`
//! (1.16.2 `0x1422222d8`), and the `E8` there calls `GetAllocator` (1.16.2 `0x141f1d190`) --
//! the game's `DLAllocator` getter, which ersc needs so its own allocations come from the
//! game's heap. The tail of the pattern is incidental context:
//!
//! ```text
//! 1.16.2  e8 b3 ae cf ff     call GetAllocator          ; 0x141f1d190
//!         48 8b 15 ...       mov rdx,[rip+..]           ; -> u"system:/"
//!         48 8d 4b 20        lea rcx,[rbx+0x20]         ; the Wwise settings' path member
//!
//! 1.17    e8 53 ad cf ff     call GetAllocator          ; 0x141f1ef90
//!         48 8b 15 ...       mov rdx,[rip+..]           ; -> u"system:/"
//!         48 8d 4b 58        lea rcx,[rbx+0x58]         ; SAME member, object grew by 0x38
//! ```
//!
//! 1.17 added 0x38 bytes of fields to that object (the zero-fill now runs `+0x10`..`+0x48` and
//! the two constants moved `+0x18`/`+0x1c` -> `+0x50`/`+0x54`), so the displacement became
//! `0x58`. One byte, and ersc's scan finds nothing.
//!
//! # What this shim does
//!
//! It does **not** rewrite `0x58` back to `0x20`: the game needs that displacement, and a
//! window where it is wrong writes a `DLString` over live fields. Instead it rebuilds the byte
//! sequence ersc is looking for in an unused `0xCC` cave, with both RIP-relative operands
//! re-based so they resolve to the *same* targets the real 1.17 site resolves to. ersc's scan
//! then finds exactly one match -- ours -- and reads the correct 1.17 `GetAllocator` out of it.
//!
//! Nothing here is version-pinned: the site, the allocator, the `u"system:/"` global and the
//! cave are all discovered by scanning the running image, so the shim survives a future patch
//! that moves them again. It refuses to act rather than guess when the image does not look the
//! way this reasoning requires (see [`shim::install`]).
//!
//! # Load order
//!
//! This DLL must be listed **before** `ersc.dll` in the me3 profile: the decoy has to exist in
//! the image before ersc scans for it. The work happens synchronously in `DllMain` for that
//! reason -- a worker thread would race the scan it is supposed to precede.

#[cfg(windows)]
mod cave;
#[cfg(windows)]
mod fixups;
#[cfg(windows)]
mod scan;
#[cfg(windows)]
mod shim;

/// `DLL_PROCESS_ATTACH`, the only `fdwReason` this DLL acts on.
const DLL_PROCESS_ATTACH: u32 = 1;
/// `DllMain` returning `TRUE`: the loader keeps the DLL loaded.
const DLL_MAIN_SUCCESS: i32 = 1;

/// # Safety
///
/// Called by the Windows loader. Do not call directly.
#[unsafe(no_mangle)]
pub unsafe extern "system" fn DllMain(
    _module: *mut core::ffi::c_void,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        #[cfg(windows)]
        shim::install();
    }
    DLL_MAIN_SUCCESS
}

/// Host-build anchor. The crate is a `cdylib` whose only real entry point is `DllMain`, which
/// does not exist off Windows; without this the non-Windows build is an empty library and the
/// module-level reasoning above never gets compiled or checked on the host.
#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_ersc_sigshim_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}
