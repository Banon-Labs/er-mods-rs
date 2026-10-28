//! Standalone ME3-loadable crash-logging DLL.
//!
//! Add this DLL as a `[[natives]]` entry beside `ersc.dll` to capture crash-like
//! first-chance exceptions into `er-crash-log.txt` / `er-crash-latest.txt` in the
//! game directory without loading the product DLL.
//!
//! It also carries the fixes for vanilla crashes that every player hits, Seamless or not, in
//! [`patches::REGISTRY`]. The first is [`patches::freelist_shutdown_assert`], the `INT3` that kills
//! the game on every quit to desktop. They live in this shell and not in `er-crash-logging-core`
//! because the product `er-quickload` links the core too, and a byte two DLLs both write is a byte
//! the second one finds already changed.

// The patch install path is `cfg(windows)`: on the host there is no game, so the registry rows
// have no caller there. That is a cfg artifact, not dead code -- the same reason
// `er-seamless-bugfixes` carries this attribute, and it is what lets `cargo test -p
// er-crash-logging` build.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

pub(crate) mod dred;
pub(crate) mod patches;

use std::sync::Once;

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_PROCESS_DETACH: u32 = 0;
const DLL_MAIN_SUCCESS: i32 = 1;

/// Main-thread stall window before the hang watchdog reports a freeze.
///
/// A lockup produces no exception at all, so without this the standalone DLL is silent through the
/// exact failure it was added beside `ersc.dll` to capture.
const HANG_STALL_SECONDS: u64 = 30;

static START: Once = Once::new();

#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. Do not call directly.
pub unsafe extern "system" fn DllMain(
    module: *mut core::ffi::c_void,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // First, before anything that can panic. A panic in a cdylib crosses an
        // `extern "system"` boundary and becomes an abort, which does not dispatch to a
        // vectored handler -- so no crash record is written at all and the process simply
        // vanishes. Enforced by `scripts/check-panic-reporter-installed.py`.
        er_game_base::panic_report::report_panics_to(
            "er-crash-logging",
            er_crash_logging_core::append_log,
        );

        START.call_once(|| {
            er_crash_logging_core::install(
                er_crash_logging_core::CrashLogConfig {
                    log_file_name: "er-crash-log.txt",
                    latest_file_name: "er-crash-latest.txt",
                    breadcrumb_file_name: "er-crash-breadcrumb-latest.txt",
                    modules_file_name: "er-crash-modules.txt",
                    minidump_file_name: "er-crash-minidump.dmp",
                    hang_report_file_name: "er-crash-hang-latest.txt",
                    hang_minidump_file_name: "er-crash-hang-minidump.dmp",
                    hang_stall_seconds: HANG_STALL_SECONDS,
                    module_label: "er-crash-logging",
                },
                module as usize,
            );
            er_crash_logging_core::write_breadcrumb(
                "dll-attach",
                format_args!("standalone loaded"),
            );
            #[cfg(windows)]
            spawn_dred_task();
            #[cfg(windows)]
            spawn_patch_task();
        });
    }
    if reason == DLL_PROCESS_DETACH {
        // Distinguishes an orderly shutdown from a process that died where it stood.
        er_crash_logging_core::note_process_detach();
    }
    DLL_MAIN_SUCCESS
}

/// Switch on D3D12's crash diagnostics off the loader thread.
///
/// Its own thread, not the patch task's: that one waits for the game module, and DRED has to be on
/// before the game creates its device. A thread spawned here starts once the loader lock is
/// released, well before the game reaches its renderer.
#[cfg(windows)]
fn spawn_dred_task() {
    let _ = std::thread::Builder::new()
        .name("er-crash-logging-dred".to_owned())
        .spawn(|| dred::start(er_crash_logging_core::append_log));
}

/// Apply the vanilla-crash patches off the loader thread, once the crash logger is installed.
///
/// Waiting on the game module and writing code do not belong under the loader lock, and the crash
/// logger goes first so a fault in this path is itself recorded.
#[cfg(windows)]
fn spawn_patch_task() {
    let _ = std::thread::Builder::new()
        .name("er-crash-logging-patches".to_owned())
        .spawn(|| {
            // A refused address is otherwise silent: every cdylib links its own er-game-base, so
            // its address logger is this DLL's to install.
            er_game_base::game_build::set_address_logger(er_crash_logging_core::append_log);
            let mut attempts = 0_u64;
            // Bounded: an unbounded spin starved the wineserver and hung a boot -- see
            // er_game_base::wait.
            let found =
                er_game_base::wait::poll_until(|| match er_game_base::mem::game_module_base() {
                    Ok(base) => Some(base),
                    Err(err) => {
                        if attempts == 0 || attempts.is_multiple_of(4096) {
                            er_crash_logging_core::append_log(format_args!(
                                "patch install: waiting for game module base: {err}"
                            ));
                        }
                        attempts = attempts.saturating_add(1);
                        None
                    }
                });
            let Some(base) = found else {
                er_crash_logging_core::append_log(format_args!(
                    "patch install: no game module base; nothing patched"
                ));
                return;
            };
            patches::install(base, er_crash_logging_core::append_log);
        });
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_crash_logging_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}
