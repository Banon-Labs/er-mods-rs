//! Cel shading for Elden Ring, as one me3 native, with an in-game menu to tune it live.
//!
//! # What it changes
//!
//! Elden Ring is deferred. Every light -- sun, tiled point and spot lights, ambient -- is
//! accumulated into one diffuse light buffer, and `GXLightDeferredAO` (or its screen-space
//! reflection twin) then multiplies ambient occlusion into it. This DLL ports the pixel shader of
//! five passes (see [`replace::REPLACEMENTS`]): the two AO passes band the light and ink
//! outlines, the albedo pass flattens surface colour, and the two tonemap passes posterize and
//! saturate the finished picture, which is what reaches forward-lit hair and fur and glowing
//! things.
//!
//! # How
//!
//! It hooks `D3D12CreateDevice`, then the device's pipeline-creation entry points. Every pipeline
//! is created as the game describes it. When one's pixel shader is a ported pass (matched by FNV-1
//! 64 of the game's DXIL), its description is copied ([`record`]) and a second pipeline, the cel
//! variant, is created from the copy with the port as its pixel shader; the pair goes into the
//! redirect table ([`table`]) that the command-list hooks ([`cmdlist`]) bind the variant through
//! whenever the game binds the port's own pipeline and the port's stage has content. Because it
//! works on what the game hands D3D12, the same DLL works on native Windows and under Proton.
//!
//! The replacement shaders are HLSL in `shaders/`, compiled and signed by
//! `scripts/build-cel-shaders.py`; the DLL embeds the result and needs no compiler at run time.
//! They read the look from a constant buffer this DLL binds ([`look_binding`]), so the menu
//! ([`menu`], default key F6) changes the picture on the next frame and saves it to
//! `er-cel-shade.toml` beside the game ([`settings`]).
//!
//! The game's own picture is always one switch away ([`safety`]): the menu's Vanilla checkbox,
//! ctrl+shift+F6 from anywhere in the game, or a stage with no content binds the game's own
//! pipelines. A worker thread runs the redirect table's reaper and writes the counters to the log
//! and to a telemetry file once a second, and two exports report and set the same state for Frida
//! ([`exports`]).
//!
//! # Evidence this rests on
//!
//! Measured 2026-10-14 under Proton with vkd3d-proton shader overrides, before this DLL existed:
//! a pass painted solid magenta covered the whole frame, and posterizing these two passes was
//! visible on screen where hardening the sun alone was not.

// The host build compiles only the pure modules; everything that calls into them is Windows-only.
#![cfg_attr(not(windows), allow(dead_code))]

pub mod cmdlist;
pub mod dxbc;
pub mod exports;
pub mod graveyard;
pub mod look_binding;
pub mod params;
pub mod probe;
pub mod record;
pub mod replace;
pub mod safety;
pub mod settings;
pub mod table;

#[cfg(windows)]
mod hooks;
#[cfg(windows)]
mod input;
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
const LOG_FILE_NAME: &str = "er-cel-shade.log";

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
    er_game_base::log::append_line(&log_path(), format_args!("er-cel-shade: {args}"));
}

/// How often the worker runs.
#[cfg(windows)]
const WORKER_PERIOD: std::time::Duration = std::time::Duration::from_secs(1);

/// The worker: everything the hooks leave for later because it takes a lock, allocates or logs.
/// Each step is cheap and decides for itself whether it is due.
#[cfg(windows)]
fn worker_step() {
    // The reaper (every 10 s) and the graveyard's wall-time fallback.
    table::worker_tick();
    // After the reaper, so the records of pipelines it dropped let go of their root signatures.
    record::prune();
    // A reaper pass can remove the last redirect.
    cmdlist::refresh_active();
    look_binding::log_ladder();
    safety::report_faults();
    // The stats line and the telemetry file.
    exports::tick();
}

#[cfg(windows)]
fn spawn_worker() {
    let spawned = std::thread::Builder::new()
        .name("er-cel-shade-worker".to_owned())
        .spawn(|| {
            let mut panicked = false;
            // The repo's pacing primitive: a channel nothing sends on, so each receive waits out
            // the period (native_overlay.rs in er-loading-portrait-core does the same).
            let (_tick_tx, tick_rx) = std::sync::mpsc::channel::<()>();
            loop {
                let _ = tick_rx.recv_timeout(WORKER_PERIOD);
                // A panic in one step must not stop the reaper and the telemetry for the rest of
                // the session; the panic hook has logged where it came from.
                if std::panic::catch_unwind(worker_step).is_err() && !panicked {
                    panicked = true;
                    log(format_args!(
                        "worker: a step panicked; the worker carries on (logged once)"
                    ));
                }
            }
        });
    if let Err(error) = spawned {
        log(format_args!(
            "worker: not started ({error}); no reaper, telemetry or stats line this session"
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
        er_game_base::panic_report::report_panics_to("er-cel-shade", log);
        let module_base = module as usize;
        START.call_once(|| {
            er_hook::set_hook_logger(log);
            log(format_args!(
                "loaded module_base=0x{module_base:x} replacements={} overlay ABI {:#06x}",
                replace::REPLACEMENTS.len(),
                er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
            ));
            // Read before the device exists, so the first frame already has the saved look.
            let (settings, warnings) = menu::load_settings();
            menu::init(settings, &warnings);
            // In `DllMain`, not on a thread: the game creates its device during start-up, and a
            // hook that lands after that sees none of the root signatures or pipelines.
            match hooks::install() {
                Ok(()) => log(format_args!("waiting for D3D12CreateDevice")),
                Err(error) => log(format_args!("not installed: {error}")),
            }
            // Neither runs until the loader lock is released; the panic chord's thread is its own
            // so it answers even when the worker or the overlay is stuck.
            spawn_worker();
            safety::spawn_panic_thread();
            // The overlay and the cursor hook take locks and enumerate modules, neither of which
            // belongs under the loader lock.
            let _ = std::thread::Builder::new()
                .name("er-cel-shade-install".to_owned())
                .spawn(move || {
                    input::install_cursor();
                    overlay::install(module_base);
                });
        });
    }
    DLL_MAIN_SUCCESS
}
