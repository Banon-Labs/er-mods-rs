//! A lower 3D render resolution with the UI at native resolution, as one me3 native, set from a
//! small in-game menu.
//!
//! # What it changes
//!
//! Elden Ring already has a dynamic resolution path: a controller that steps the scene's scale
//! between a minimum and a maximum from the GPU's frame time, a view setup that draws the scene
//! into a viewport that fraction of the output, and a final composite that upsamples the scene
//! under a UI drawn at the output's size. On PC it runs between 0.70 and 1.00 and, under the
//! 33.3 ms budget, never leaves 1.00. This DLL detours the controller's per-frame update
//! ([`drs`]) and pins its minimum and maximum to `1/divisor`, so the scene is drawn at the output
//! size divided by a whole number the player picks, and the UI stays sharp.
//!
//! The mechanism was read statically (bd `er-dynamic-resolution-chain-static-2026-10-15`) and
//! proven live with Frida before this DLL existed (bd
//! `er-dynamic-resolution-runtime-proof-2026-10-15`, `scripts/frida/render-scale.js`).
//!
//! # The menu
//!
//! The menu key (F5 by default, [`settings::DEFAULT_MENU_KEY`]) opens a small panel ([`menu`])
//! drawn in the shared theme as a guest of the process's one imgui overlay ([`overlay`]). Its
//! slider steps through exactly the divisors that map the scene onto the screen in whole pixels
//! ([`scale`]), labelled with the scene size each draws, and it shows the size the game actually
//! drew and the game's GPU time ([`report`]). While it is open the camera holds still, the cursor
//! is free, and the mouse, the keys and the pad go to the menu, not the game
//! (`er-overlay-input`). The menu key or Escape closes it.
//!
//! The menu key and the divisor live in `er-render-scale.toml` beside the game ([`settings`],
//! [`config`]). The divisor saved there is applied at boot, and an edit to the file takes effect
//! within a second.
//!
//! # 2.7.2.0 only
//!
//! The update's address and opening bytes were read out of 2.7.2.0, and the DLL refuses every other
//! build and any other bytes rather than write through a stale address; the menu then shows why.
//!
//! # Telemetry
//!
//! Once a second a worker writes `er-render-scale-telemetry.json` ([`report::TELEMETRY_FILE_NAME`],
//! redirected by `ER_QUICKLOAD_RENDER_SCALE_TELEMETRY_PATH`): the divisor asked for and in force,
//! the output size, the size the game drew and whether it is exact, the GPU time, the controller's
//! fields, and the menu's state and input capture. `scripts/check-render-scale-probe.py` reads it.

// The host build compiles only the pure modules; everything that calls into them is Windows-only.
#![cfg_attr(not(windows), allow(dead_code))]

pub mod report;
pub mod scale;
pub mod settings;

#[cfg(windows)]
mod config;
#[cfg(windows)]
mod drs;
#[cfg(windows)]
mod menu;
#[cfg(windows)]
mod overlay;

#[cfg(windows)]
use std::sync::Once;

/// `DLL_PROCESS_ATTACH`.
#[cfg(windows)]
const DLL_PROCESS_ATTACH: u32 = 1;

/// `DllMain` must return true or the loader unloads us.
#[cfg(windows)]
const DLL_MAIN_SUCCESS: i32 = 1;

/// Log file name, beside the game executable like every other shell in this repo.
#[cfg(windows)]
const LOG_FILE_NAME: &str = "er-render-scale.log";

#[cfg(windows)]
static START: Once = Once::new();

#[cfg(windows)]
fn log_path() -> std::path::PathBuf {
    er_game_base::log::game_directory_path()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(LOG_FILE_NAME)
}

#[cfg(windows)]
pub(crate) fn log(args: std::fmt::Arguments<'_>) {
    er_game_base::log::append_line(&log_path(), format_args!("er-render-scale: {args}"));
}

/// How often the worker runs.
#[cfg(windows)]
const WORKER_PERIOD: std::time::Duration = std::time::Duration::from_secs(1);

/// One telemetry document, as of now.
#[cfg(windows)]
fn snapshot(serial: u64) -> report::Report {
    use er_game_base::game_build::is_supported_build;
    let readings = drs::read();
    let hook = drs::hook_state();
    let view = report::view(drs::selected(), drs::applied(), &readings);
    report::Report {
        pid: std::process::id(),
        written_unix_ms: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |since| {
                u64::try_from(since.as_millis()).unwrap_or(u64::MAX)
            }),
        serial,
        // The one build the update's address was measured on, 2.7.2.0.
        game_build_ok: is_supported_build(),
        hook,
        readings,
        view,
        menu: menu::state(),
        settings: config::state(),
    }
}

/// The worker: the settings file and the telemetry, each cheap and each due once a second.
#[cfg(windows)]
fn worker_step(serial: u64) {
    drs::retry();
    config::tick();
    use report::{TELEMETRY_FILE_NAME, TELEMETRY_PATH_ENV};
    let text = snapshot(serial).render();
    // The constants by their bare names: `scripts/er-artifact-redirect-audit.py` reads the knob
    // out of this call, and requires every launcher to redirect it into the run's directory.
    let path = er_game_base::log::redirected_artifact_path(TELEMETRY_PATH_ENV, TELEMETRY_FILE_NAME);
    if let Err(error) = er_hotkey_config::write_atomic(&path, &text) {
        static WARNED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
        if !WARNED.swap(true, std::sync::atomic::Ordering::Relaxed) {
            log(format_args!(
                "telemetry: writing {} failed: {error} (logged once)",
                path.display()
            ));
        }
    }
}

#[cfg(windows)]
fn spawn_worker() {
    let spawned = std::thread::Builder::new()
        .name("er-render-scale-worker".to_owned())
        .spawn(|| {
            let mut panicked = false;
            let mut serial = 0_u64;
            // The repo's pacing primitive: a channel nothing sends on, so each receive waits out
            // the period.
            let (_tick_tx, tick_rx) = std::sync::mpsc::channel::<()>();
            loop {
                let _ = tick_rx.recv_timeout(WORKER_PERIOD);
                serial += 1;
                // A panic in one step must not stop the telemetry for the rest of the session; the
                // panic hook has logged where it came from.
                if std::panic::catch_unwind(|| worker_step(serial)).is_err() && !panicked {
                    panicked = true;
                    log(format_args!(
                        "worker: a step panicked; the worker carries on (logged once)"
                    ));
                }
            }
        });
    if let Err(error) = spawned {
        log(format_args!(
            "worker: not started ({error}); no settings saves or telemetry this session"
        ));
    }
}

// Every shell that links the overlay host exports its registration, so a guest can find whichever
// module won the overlay.
#[cfg(windows)]
er_build_watermark_core::export_overlay_host!();

#[cfg(windows)]
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
        // Before anything that can panic: a panic in a hook crosses an `extern "system"` boundary
        // and aborts without a crash record unless this is installed.
        er_game_base::panic_report::report_panics_to("er-render-scale", log);
        let module_base = module as usize;
        START.call_once(|| {
            er_hook::set_hook_logger(log);
            log(format_args!(
                "loaded module_base=0x{module_base:x} overlay ABI {:#06x}",
                er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
            ));
            // Read before the game's draw context exists, so the saved divisor is in force on the
            // first update that can take it.
            config::init();
            // The detour, the overlay and the cursor hook take locks and enumerate modules, none
            // of which belongs under the loader lock.
            let _ = std::thread::Builder::new()
                .name("er-render-scale-install".to_owned())
                .spawn(move || {
                    if let Err(reason) = drs::install() {
                        log(format_args!(
                            "the scale cannot be applied this session: {reason}"
                        ));
                    }
                    er_overlay_input::install_cursor(log);
                    overlay::install(module_base);
                    log(format_args!(
                        "install done: overlay joined {}",
                        overlay::joined()
                    ));
                });
            spawn_worker();
        });
    }
    DLL_MAIN_SUCCESS
}
