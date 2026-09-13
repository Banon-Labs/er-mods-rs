#[cfg(not(windows))]
pub fn host_diagnostic_stub() {}

// Deliberately outside the `#[cfg(windows)]` block below: the `05_010_ProfileSelect` chrome
// decision is a pure predicate, and the gate it replaces removed a user-facing surface without
// failing to build and without logging anything, so it earned a place a host test can reach.
pub mod profile_select_chrome_gate;

// Deliberately outside the `#[cfg(windows)]` block below: which menu window a `System>Quit` switch
// left behind on the title, and why it is the only one this crate may ask to close, is a pure
// decision whose tests run on the host -- where every other gate in this crate is only ever
// type-checked by the cross-compile.
pub mod orphan_title_window;

// Deliberately outside the `#[cfg(windows)]` block below: what a boot autoload is allowed to hide
// -- the logo splash, the online-mode getter, the pre-world sound -- is a pure decision, and all
// three covers shipped in a build that could not autoload because each asked a question with no
// term for the autoload in it.
pub mod autoload_cover_gates;

#[cfg(windows)]
use std::{
    ffi::c_void,
    fs,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    time::Instant,
};

#[cfg(windows)]
use eldenring::{
    cs::{CSTaskGroupIndex, CSTaskImp, GameMan, PlayerIns},
    fd4::FD4TaskData,
};
#[cfg(windows)]
use er_save_loader::{SaveLoadContext, SaveLoader};
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, InstanceError, SharedTaskImpExt};
#[cfg(windows)]
use windows::{
    Win32::{
        Foundation::HINSTANCE,
        System::{
            LibraryLoader::{GetProcAddress, LoadLibraryA},
            SystemServices::DLL_PROCESS_ATTACH,
        },
    },
    core::PCSTR,
};

#[cfg(windows)]
mod config;
#[cfg(windows)]
mod constants;
#[cfg(windows)]
mod crashlog;
#[cfg(windows)]
mod experiments;
#[cfg(windows)]
mod ffi;
#[cfg(windows)]
mod hooks;
#[cfg(windows)]
mod input_blocker;
// Not `#[cfg(windows)]`: a pure predicate with no game in it, so its tests run on the host with
// `cargo test -p er-quickload --lib`. That is the whole reason it is a module of its own rather
// than a line inside the game task.
pub mod menu_window_run_gate;
#[cfg(windows)]
mod mh;
#[cfg(windows)]
mod telemetry;

#[cfg(windows)]
include!("lib_parts/dll_entry.rs");
#[cfg(windows)]
include!("lib_parts/runtime_helpers.rs");
