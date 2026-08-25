//! ME3 shell that imports the `er-effects.toml` `build_url` build once, at character load.
//!
//! All of the work lives in `er-build-import-runtime`; this crate is the standalone trigger for it.
//! The product DLL (`er-effects-rs`) drives the SAME runtime from its System>Quit "Load Build from
//! URL" row instead, so the two must never share a profile -- see `scripts/me3-dll-conflicts.toml`.
//!
//! Nothing here sleeps: `DllMain` spawns two threads and returns, the fetch blocks in WinHTTP, and
//! the game task re-checks its preconditions once a frame.

#![cfg(windows)]

use windows::Win32::Foundation::{HINSTANCE, TRUE};
use windows::Win32::System::SystemServices::DLL_PROCESS_ATTACH;

/// DLL entry point. Spawns the request and the task registrar, and returns.
///
/// # Safety
///
/// Called by the loader. Nothing slow or reentrant runs under the loader lock.
#[unsafe(no_mangle)]
pub extern "system" fn DllMain(_module: HINSTANCE, reason: u32, _reserved: *mut ()) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        std::thread::spawn(start_configured_import);
        std::thread::spawn(register_task);
    }
    TRUE.0
}

/// Ask the runtime for the configured build. Off the loader thread because reading the config file
/// touches the filesystem.
fn start_configured_import() {
    match er_build_import_runtime::request_configured() {
        Ok(true) => {}
        // No `build_url` set: the runtime already logged that, and doing nothing is correct.
        Ok(false) => {}
        Err(err) => er_build_import_runtime::log_line(&format!(
            "[build-import] configured import refused: {err}"
        )),
    }
}

/// Register the FrameBegin task that owns every game-touching step.
fn register_task() {
    use eldenring::cs::{CSTaskGroupIndex, CSTaskImp};
    use eldenring::fd4::FD4TaskData;
    use fromsoftware_shared::{FromStatic, SharedTaskImpExt};

    let task = loop {
        match unsafe { CSTaskImp::instance() } {
            Ok(task) => break task,
            // No sleep (banned by scripts/check-no-timeouts.py): yield and re-poll, the same shape
            // er-invasion-warp and er-telemetry use.
            Err(_) => std::thread::yield_now(),
        }
    };
    er_build_import_runtime::log_line(
        "[build-import] CSTaskImp resolved; registering FrameBegin import task",
    );

    let handle = task.run_recurring(
        move |_data: &FD4TaskData| {
            // Safety: this closure runs on the game task thread, which is the context the runtime's
            // tick requires; each step inside it is individually precondition-checked.
            let _ = unsafe { er_build_import_runtime::tick() };
        },
        CSTaskGroupIndex::FrameBegin,
    );
    // The handle cancels the task on drop, and the task must outlive this bootstrap thread.
    std::mem::forget(handle);
}
