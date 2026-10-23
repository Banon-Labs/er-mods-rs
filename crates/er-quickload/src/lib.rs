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
        Arc, Mutex, OnceLock,
        atomic::{AtomicU64, AtomicUsize, Ordering},
    },
    time::Instant,
};

#[cfg(windows)]
use eldenring::{
    cs::{CSTaskGroupIndex, CSTaskImp, GameMan, PlayerIns},
    fd4::FD4TaskData,
};
#[cfg(windows)]
use er_save_loader::{GameManTelemetry, SaveLoadContext, SaveLoader};
#[cfg(windows)]
use fromsoftware_shared::{FromStatic, InstanceError, SharedTaskImpExt};
#[cfg(windows)]
use windows::{
    Win32::{
        Foundation::HINSTANCE,
        System::{
            LibraryLoader::{GetModuleHandleA, GetProcAddress, LoadLibraryA},
            SystemServices::DLL_PROCESS_ATTACH,
            Threading::GetCurrentProcessId,
        },
        UI::WindowsAndMessaging::GetWindowThreadProcessId,
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

// The modules below lived under `src/experiments/` until 2026-10-22. That directory held 76% of
// the crate, most of it product code, and its name said the opposite.
#[cfg(windows)]
mod save_redirect;
#[cfg(windows)]
pub(crate) use save_redirect::*;

// Diagnostics, and the first subsystem behind a feature (2026-09-11). Two external references in
// 4,300 lines, so it is the cheapest proof that this crate can be compiled as a subset at all.
#[cfg(all(windows, feature = "menu-trace"))]
mod trace;
#[cfg(all(windows, feature = "menu-trace"))]
pub(crate) use trace::*;

#[cfg(windows)]
mod startup_hooks;
#[cfg(windows)]
pub(crate) use startup_hooks::*;

#[cfg(windows)]
mod gpu_readback;
#[cfg(windows)]
pub(crate) use gpu_readback::*;

#[cfg(windows)]
mod present_overlay;
#[cfg(windows)]
pub(crate) use present_overlay::*;

#[cfg(windows)]
mod gpu_frame_timing;
#[cfg(windows)]
pub(crate) use gpu_frame_timing::*;

// native_overlay moved to the er-loading-portrait-core crate (portrait crate split); the
// explicit re-exports keep the product call sites (lifecycle.rs, telemetry oracles)
// compiling unchanged. The rest of the crate's surface flows in through the glob shims
// at the top of gpu_readback.rs / startup_hooks.rs.
#[cfg(windows)]
pub(crate) use er_loading_portrait_core::{NATIVE_OVERLAY_SHOW, install_native_overlay};

#[cfg(windows)]
pub(crate) mod can_move_probe;
#[cfg(windows)]
pub(crate) mod input_block;
#[cfg(windows)]
pub(crate) use input_block::*;

#[cfg(windows)]
mod input_trace;
#[cfg(windows)]
pub(crate) use input_trace::*;

#[cfg(windows)]
mod own_load;
#[cfg(windows)]
pub(crate) use own_load::*;

#[cfg(windows)]
mod menu_diag;
#[cfg(windows)]
pub(crate) use menu_diag::*;

#[cfg(windows)]
mod mem;
#[cfg(windows)]
pub(crate) use mem::*;

#[cfg(windows)]
mod gating;
#[cfg(windows)]
pub(crate) use gating::*;

#[cfg(windows)]
mod own_stepper;
#[cfg(windows)]
pub(crate) use own_stepper::*;

#[cfg(windows)]
mod continue_load;
#[cfg(windows)]
pub(crate) use continue_load::*;

#[cfg(windows)]
mod save_picker;

#[cfg(windows)]
mod lifecycle;
#[cfg(windows)]
pub(crate) use lifecycle::*;

#[cfg(windows)]
mod product_core_own_stepper;
#[cfg(windows)]
pub(crate) use product_core_own_stepper::*;

#[cfg(windows)]
include!("lib_parts/dll_entry.rs");
#[cfg(windows)]
include!("lib_parts/runtime_helpers.rs");
